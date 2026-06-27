#!/usr/bin/env python3
"""
TikTok Auto Bot — usage: python main.py <N>
Generates N videos then uploads them all, then exits.
"""

import argparse
import json
import logging
import logging.handlers
import os
import queue
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from config import get_config
from scraper import ContentScraper, title_fingerprint
from tts import TextToSpeech
from video_gen import VideoAssembler, MIN_DURATION
from uploader import TikTokUploader
from whisper_sub import WhisperSubtitler
from splitter import split_script, estimate_duration_seconds


# Candidates supplémentaires récupérées en plus des n histoires visées : sert de
# réserve de remplaçantes quand une histoire est écartée (trop courte) — on change
# d'histoire au lieu de combler la vidéo avec du silence.
CANDIDATE_BUFFER = 5

# Génération en parallèle : nombre de vidéos traitées SIMULTANÉMENT. Plafonné
# pour garder le CPU sous contrôle (encodages ffmpeg concurrents + Whisper).
MAX_PARALLEL_VIDEOS = 5

# Hashtags par type de contenu (stratégie de croissance TikTok US).
# Volontairement 4 hashtags NICHE et pertinents. Les tags génériques
# (#fyp #foryoupage #viral) sont retirés : ils n'aident pas la portée et sont
# un signal de spam/bot fort pour TikTok → favorisent le shadowban.
HASHTAGS = {
    'drama': '#reddit #redditstories #aita #storytime',
    'facts': '#reddit #todayilearned #didyouknow #funfacts',
}

def _setup_logging(log_level: str, log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    # Évite les UnicodeEncodeError sur consoles Windows cp1252.
    try:
        console.stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    rotating = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    rotating.setFormatter(fmt)

    root = logging.getLogger()
    # Idempotent : retire nos propres handlers (console/fichier) pour éviter les
    # doublons quand run() rappelle _setup_logging via _reset_output. On PRÉSERVE
    # les handlers tiers (ex: le QueueHandler de la GUI qui alimente la console).
    for h in root.handlers[:]:
        if isinstance(h, (logging.StreamHandler, logging.handlers.RotatingFileHandler)) \
                and not isinstance(h, logging.handlers.QueueHandler):
            # StreamHandler couvre aussi nos consoles ; on ne touche pas au reste.
            if getattr(h, '_tiktokbot', False):
                h.close()
                root.removeHandler(h)
    console._tiktokbot = True
    rotating._tiktokbot = True
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.addHandler(console)
    root.addHandler(rotating)

    # Réduit le bruit des bibliothèques tierces (numba/torch très verbeux en DEBUG).
    for noisy in ('numba', 'torch', 'urllib3', 'asyncio', 'praw', 'prawcore'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


class TikTokAutoBot:

    def __init__(self):
        self.config = get_config()

        _setup_logging(self.config.LOG_LEVEL, self.config.LOG_DIR / 'bot.log')

        if not self.config.validate():
            raise SystemExit(1)

        self.scraper = ContentScraper(self.config)
        self.tts = TextToSpeech(self.config)
        self.subtitler = WhisperSubtitler(self.config)
        self.assembler = VideoAssembler(self.config)
        self.uploader = TikTokUploader(self.config)

        self.processed_ids, self.seen_titles = self._load_history()
        self._stop_event = None
        # Compteur d'histoires RÉUSSIES : un numéro n'est attribué qu'une fois
        # qu'une histoire produit au moins une vidéo. Évite les trous dans le
        # nommage (001, 003…) causés par les candidates écartées (trop courtes).
        self._story_counter = 0
        self._story_counter_lock = threading.Lock()
        logger.info(f'Bot initialized | {len(self.processed_ids)} IDs / '
                    f'{len(self.seen_titles)} titres déjà vus (anti-répétition)')

    def _load_history(self) -> tuple:
        """Charge l'historique anti-répétition : (set d'IDs, set d'empreintes de titres).

        Tolère l'ancien format (simple liste d'IDs) et l'ancien emplacement
        (output/processed_ids.json) — migrés automatiquement à la 1re sauvegarde.
        """
        path = Path(self.config.PROCESSED_IDS_FILE)
        legacy = Path(getattr(self.config, 'LEGACY_PROCESSED_IDS_FILE', path))
        src = path if path.exists() else (legacy if legacy.exists() else None)
        if src is None:
            return set(), set()
        try:
            with open(src, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):  # ancien format : liste d'IDs uniquement
                return set(data), set()
            return set(data.get('ids', [])), set(data.get('titles', []))
        except Exception:
            logger.warning('historique illisible — on repart de zéro')
            return set(), set()

    def _save_history(self):
        """Persiste l'historique (IDs + empreintes de titres) hors de output/,
        donc à l'abri de _reset_output(). Écriture atomique (tmp + replace)."""
        path = Path(self.config.PROCESSED_IDS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        data = {
            'ids': list(self.processed_ids)[-5000:],     # cap mémoire/disque
            'titles': list(self.seen_titles)[-5000:],
        }
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except Exception as e:
            logger.error(f'Échec sauvegarde historique : {e}')

    def _mark_used(self, item: dict):
        """Enregistre une histoire comme utilisée (IDs Reddit + empreinte de
        titre) pour qu'elle ne se répète jamais."""
        self.processed_ids.update(item.get('consumed_ids') or [item['id']])
        if item.get('content_strategy') == 'story':
            fp = title_fingerprint(item.get('title', ''))
            if fp:
                self.seen_titles.add(fp)

    def _reset_output(self):
        # Ferme le handler fichier avant de supprimer le dossier (Windows : fichier verrouillé).
        root = logging.getLogger()
        for h in root.handlers[:]:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                h.close()
                root.removeHandler(h)

        output_dir = Path(self.config.OUTPUT_DIR)
        if output_dir.exists():
            shutil.rmtree(output_dir)

        for d in [self.config.OUTPUT_DIR, self.config.AUDIO_DIR,
                  self.config.VIDEO_DIR, self.config.SUBS_DIR, self.config.LOG_DIR]:
            Path(d).mkdir(parents=True, exist_ok=True)

        _setup_logging(self.config.LOG_LEVEL, self.config.LOG_DIR / 'bot.log')
        logger.info('Dossier output réinitialisé.')

    def _hashtag_key(self, item: dict) -> str:
        if item.get('content_strategy') == 'facts' \
                or 'todayilearned' in item.get('subreddit', ''):
            return 'facts'
        return 'drama'

    def _caption_for(self, item: dict, part_label: Optional[str] = None) -> str:
        hook = item.get('hook', item['title'])[:100]
        tags = HASHTAGS.get(self._hashtag_key(item), '')
        label = f' ({part_label})' if part_label else ''
        return f'{hook}{label} {tags}'[:300]

    def _write_metadata(self, generated: list) -> Optional[str]:
        """Écrit un unique fichier récap (output/_TIKTOK_CAPTIONS.txt) regroupant
        les descriptions de toutes les vidéos. Chaque partie = un post TikTok distinct.

        Pour chaque vidéo : une description simple = titre de l'histoire Reddit
        suivi des hashtags. Retourne le chemin du récapitulatif.
        """
        summary_lines = ['=' * 60,
                         'TIKTOK — À COPIER-COLLER POUR CHAQUE VIDÉO',
                         '=' * 60, '']

        for item, video_path, part_label in generated:
            stem = Path(video_path).stem  # ex. 001_02
            key = self._hashtag_key(item)
            tags = HASHTAGS.get(key, '')

            # Description = titre de l'histoire tiré de Reddit. Pour les facts
            # (titre générique "Did You Know?"), on prend le hook à la place.
            reddit_title = (item.get('title') or '').strip()
            if not reddit_title or reddit_title.lower() == 'did you know?':
                reddit_title = (item.get('hook') or '').strip()
            part_tag = f' [{part_label}]' if part_label else ''

            block = f'{reddit_title}{part_tag}\n\n{tags}'

            summary_lines += [f'### {stem}.mp4', block, '', '-' * 60, '']

        # Récap global à la racine de output/ (visible directement, pas enfoui
        # dans output/videos/ avec les .mp4).
        summary_path = Path(self.config.OUTPUT_DIR) / '_TIKTOK_CAPTIONS.txt'
        try:
            summary_path.write_text('\n'.join(summary_lines), encoding='utf-8')
            logger.info(f'Métadonnées TikTok écrites : {summary_path}')
        except Exception as e:
            logger.warning(f'Écriture récapitulatif métadonnées échouée : {e}')
            return None
        return str(summary_path)

    def _claim_story_number(self) -> int:
        """Attribue le prochain numéro d'histoire (réservé aux histoires qui
        produisent réellement une vidéo). Atomique → sûr en génération parallèle."""
        with self._story_counter_lock:
            self._story_counter += 1
            return self._story_counter

    def _process_item(self, item: dict, story_num: int, on_part_done=None,
                      on_stage=None) -> list:
        """Génère une ou plusieurs vidéos (parties) à partir d'un item.

        `story_num` : numéro de la candidate (ordre de soumission) — utilisé
        UNIQUEMENT pour les logs. Le numéro de fichier (NNN) n'est attribué qu'à
        la 1re partie réellement produite, via _claim_story_number(), pour un
        nommage sans trous : ``001_01.mp4``, ``002_01.mp4``…

        Retourne une liste de tuples (item, video_path, part_label).
        `on_part_done()` est appelé après chaque partie terminée (pour la barre
        de progression).
        """
        script = item['tts_script']

        # Découpage en parties si histoire longue.
        if (self.config.AUTO_SPLIT
                and item.get('content_strategy') == 'story'
                and estimate_duration_seconds(script) > self.config.SPLIT_MAX_DURATION):
            script_parts = split_script(script)
        else:
            script_parts = [script]

        total = len(script_parts)
        results = []
        # Numéro de fichier (NNN) attribué paresseusement à la 1re partie qui
        # passe le seuil de durée. Toutes les parties d'une même histoire le
        # partagent → nommage NNN_MM SANS TROUS (priorité : zéro saut de numéro,
        # même quand des candidates sont écartées).
        num = None

        for idx, part_script in enumerate(script_parts, start=1):
            if self._stop_event and self._stop_event.is_set():
                logger.info('Arrêt demandé — génération interrompue.')
                break
            part_label = f'Part {idx}/{total}' if total > 1 else None
            ptag = f' P{idx}/{total}' if total > 1 else ''

            # Voix : on ne connaît le numéro définitif qu'après le 1er succès.
            # Tant qu'on ne l'a pas, on génère avec la voix de la candidate ;
            # une fois `num` connu, les parties suivantes suivent l'alternance
            # H/F basée sur `num` (cohérent avec l'ordre des vidéos produites).
            if on_stage:
                on_stage(f'Voix{ptag}')
            voice_idx = (num - 1) if num is not None else (story_num - 1)
            # Audio temporaire indexé sur la candidate tant que le numéro
            # définitif n'est pas réservé ; on régénère sous le bon nom ensuite.
            stem = f'{num:03d}_{idx:02d}' if num is not None else f'cand{story_num:03d}_{idx:02d}'
            voice = self.tts.voice_for_index(voice_idx)
            audio_path = self.tts.generate_tts(part_script, stem, voice=voice)
            if audio_path is None:
                logger.error(f'TTS échoué pour candidate {story_num} partie {idx} — ignoré')
                continue

            # On ne comble JAMAIS au silence : si la narration est trop courte
            # (< MIN_DURATION), on jette cette partie. Une histoire qui ne produit
            # aucune partie sera remplacée par une autre (cf. run()).
            dur = self.assembler.probe_duration(audio_path)
            if dur is not None and dur < MIN_DURATION:
                logger.info(f'Partie trop courte ({dur:.1f}s < '
                            f'{MIN_DURATION:.0f}s) — ignorée (pas de silence ajouté).')
                Path(audio_path).unlink(missing_ok=True)
                continue

            # 1re partie valide de l'histoire → on réserve le numéro définitif.
            # Si l'audio a été généré sous un nom temporaire (cand…), on le
            # renomme vers NNN_MM pour des fichiers de sortie propres.
            if num is None:
                num = self._claim_story_number()
                final_stem = f'{num:03d}_{idx:02d}'
                new_audio = Path(audio_path).with_name(f'tts_{final_stem}.mp3')
                try:
                    Path(audio_path).replace(new_audio)
                    audio_path = str(new_audio)
                except OSError:
                    pass  # garde le nom temporaire si le renommage échoue
            part_id = f'{num:03d}_{idx:02d}'

            # Carte Reddit (titre dans un cadre blanc) uniquement sur la partie 1.
            card_title = card_subreddit = None
            if idx == 1:
                t = (item.get('title') or '').strip()
                if not t or t.lower() == 'did you know?':
                    t = (item.get('hook') or '').strip()
                card_title = t or None
                card_subreddit = item.get('subreddit')

            if on_stage:
                on_stage(f'Sous-titres{ptag}')
            ass_path, card_end_time = self.subtitler.generate(
                audio_path, part_id, card_title=card_title)
            if ass_path is None:
                logger.warning(f'Whisper failed for {part_id}, continuing without subs')

            if on_stage:
                on_stage(f'Montage{ptag}')
            video_path = self.assembler.assemble(
                audio_path, part_id, ass_path=ass_path,
                reddit_title=card_title, subreddit=card_subreddit,
                card_end_time=card_end_time,
            )

            for tmp in (audio_path, ass_path):
                if tmp:
                    Path(tmp).unlink(missing_ok=True)

            if video_path is None:
                logger.error(f'Assemblage échoué pour {part_id} — ignoré')
                continue

            results.append((item, video_path, part_label))
            logger.info(f'Vidéo prête : {video_path}'
                        + (f' [{part_label}]' if part_label else ''))
            if on_part_done:
                on_part_done()

        return results

    def run(self, n: int, content_type: Optional[str] = None, stop_event=None,
            progress_callback=None, activity_callback=None):
        """progress_callback(done, total) : appelé à chaque vidéo terminée et au
        démarrage avec total estimé, pour alimenter la barre de progression.
        activity_callback(slot, texte) : signale ce que fait le worker `slot`
        (0..MAX_PARALLEL_VIDEOS-1) — alimente l'affichage des terminaux parallèles
        ; texte vide = worker au repos."""
        self._stop_event = stop_event
        content_type = content_type or self.config.CONTENT_TYPE
        logger.info(f'=== Démarrage : {n} histoire(s), type={content_type} ===')
        self._reset_output()
        # Repart de 001 à chaque run (le dossier output a été vidé).
        with self._story_counter_lock:
            self._story_counter = 0

        # Phase 1 : récupération du contenu. On passe l'historique complet
        # (IDs + empreintes de titres) pour écarter toute histoire déjà utilisée.
        # On sur-échantillonne (n + CANDIDATE_BUFFER) pour avoir des remplaçantes :
        # une histoire trop courte est écartée et REMPLACÉE plutôt que comblée.
        items = self.scraper.get_content(
            limit=n + CANDIDATE_BUFFER,
            seen_ids=self.processed_ids,
            content_type=content_type,
            seen_titles=self.seen_titles,
        )

        if not items:
            logger.error('Aucun contenu récupéré — abandon')
            return

        logger.info(f'Phase 1 : {len(items)} candidate(s) pour {n} histoire(s)…')
        generated = []  # list of (item, video_path, part_label)

        # Estimation du total de vidéos pour la barre de progression : ~1 vidéo
        # par histoire visée ; chaque partie terminée réajuste l'estimation.
        est_total = max(n, 1)
        done_count = [0]
        if progress_callback:
            progress_callback(0, est_total)

        lock = threading.Lock()

        def _part_done():
            with lock:
                done_count[0] += 1
                done = done_count[0]
            if progress_callback:
                progress_callback(done, max(est_total, done))

        # Génération EN PARALLÈLE (jusqu'à MAX_PARALLEL_VIDEOS histoires en même
        # temps). `cand_num` = rang de la candidate (logs uniquement) ; le numéro
        # de fichier (NNN) est attribué à la 1re partie réussie (cf. _process_item)
        # → aucun trou. On s'arrête à n réussites, et les vidéos produites en trop
        # par les workers encore en vol sont supprimées (aucune orpheline).
        stories_done = [0]
        workers = max(min(MAX_PARALLEL_VIDEOS, n), 1)
        # Pool de "slots" (un par terminal affiché) : chaque worker actif en prend
        # un et le rend en fin de traitement → l'UI sait quel terminal fait quoi.
        slots = queue.Queue()
        for s in range(workers):
            slots.put(s)

        def _process_candidate(item, cand_num):
            if stop_event and stop_event.is_set():
                return
            with lock:
                if stories_done[0] >= n:
                    return
            slot = slots.get()
            title = (item.get('title') or '?')[:30]

            def _stage(label):
                if activity_callback:
                    activity_callback(slot, f'#{cand_num} {title} — {label}')

            try:
                _stage('Préparation')
                logger.info(f'[{item.get("source", "?")}] {item["title"][:60]}')
                results = self._process_item(item, story_num=cand_num,
                                             on_part_done=_part_done, on_stage=_stage)
                # On ne marque comme « utilisée » QUE l'histoire dont une vidéo est
                # effectivement conservée. Une candidate écartée (trop courte, échec
                # TTS/montage) ou produite en trop (objectif déjà atteint) N'EST PAS
                # enregistrée → l'historique ne contient que les histoires réellement
                # transformées en vidéos, pas les simples candidates récupérées.
                if not results:
                    logger.info('Histoire écartée — candidate suivante.')
                    return
                with lock:
                    if stories_done[0] < n:
                        generated.extend(results)
                        stories_done[0] += 1
                        self._mark_used(item)
                        return
                for _it, vpath, _lbl in results:  # objectif atteint entre-temps
                    Path(vpath).unlink(missing_ok=True)
            finally:
                if activity_callback:
                    activity_callback(slot, '')  # terminal au repos
                slots.put(slot)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_process_candidate, item, i + 1)
                       for i, item in enumerate(items)]
            for _ in as_completed(futures):
                pass

        if not generated:
            logger.error('Aucune vidéo générée.')
            return

        self._save_history()

        # Métadonnées TikTok prêtes à copier-coller (titre, description,
        # hashtags, crédit auteur + disclosure IA) — un .txt par vidéo.
        self._write_metadata(generated)

        # Upload désactivé pour le moment — on conserve les vidéos générées.
        token = (self.config.TIKTOK_SESSION_ID or '').strip()
        if not token or token in ('your_session_id_here', 'your_access_token_here'):
            logger.info(f'=== Terminé : {len(generated)} vidéo(s) dans '
                        f'{self.config.VIDEO_DIR} ===')
            return

        # Phase 2: Upload
        logger.info(f'Phase 2 : upload de {len(generated)} vidéo(s)…')

        for i, (item, video_path, part_label) in enumerate(generated):
            caption = self._caption_for(item, part_label)
            logger.info(f'Upload [{i+1}/{len(generated)}] : {caption[:60]}')

            success = self.uploader.upload(video_path, caption)

            if success:
                # Déjà marqué utilisé à la génération (_process_candidate) —
                # inutile de re-marquer ici.
                try:
                    Path(video_path).unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                logger.warning(f'Upload échoué — vidéo conservée : {video_path}')

            if i < len(generated) - 1:
                logger.info('Attente 30s avant le prochain upload…')
                time.sleep(30)

        self._save_history()
        logger.info(f'=== Run terminé : {len(generated)} vidéo(s) traitée(s) ===')


def main():
    parser = argparse.ArgumentParser(description='TikTok Auto Bot')
    parser.add_argument('n', type=int, help='Nombre de vidéos à générer')
    parser.add_argument('--type', dest='content_type', default=None,
                        choices=['drama', 'facts', 'rotate'],
                        help='Type de contenu (défaut : valeur du .env)')
    args = parser.parse_args()

    if args.n < 1:
        parser.error('N doit être un entier positif')

    bot = TikTokAutoBot()
    bot.run(args.n, content_type=args.content_type)


if __name__ == '__main__':
    main()
