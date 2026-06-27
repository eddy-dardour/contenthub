#!/usr/bin/env python3
"""
Sous-titrage automatique via Whisper (hors-ligne).

Transcrit l'audio TTS avec timestamps mot-par-mot, puis génère un fichier
`.ass` au style TikTok viral : Komika Axis, gros texte blanc, contour
noir épais, légère apparition (fade) et mot courant surligné en jaune.
"""

import io
import logging
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Mots affichés simultanément (lisible en ~1s sur mobile).
WORDS_PER_SEGMENT = 3

# Durée du fondu d'apparition/disparition par segment (millisecondes).
FADE_MS = 60


def _ensure_std_streams():
    """En mode PyInstaller --windowed, sys.stdout/stderr valent None.
    Whisper (tqdm) écrit dessus et plante : on fournit des flux factices."""
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _fmt_time(seconds: float) -> str:
    """Secondes → timestamp ASS H:MM:SS.cs (centisecondes)."""
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_header() -> str:
    """En-tête + style TikTok.

    Couleurs ASS = &HAABBGGRR :
      PrimaryColour &H00FFFFFF  → texte blanc
      OutlineColour &H00000000  → contour noir
      Fontsize 155, Bold, Outline 8, Shadow 4 → gros, lisible, contrasté
      Alignment 5 → centré au milieu de l'écran (horizontal + vertical)
    """
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Komika Axis,155,&H00FFFFFF,&H00000000,"
        "&H00000000,1,1,8,4,5,60,60,80,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )


def _words_to_segments(words: List[dict], size: int = WORDS_PER_SEGMENT) -> List[List[dict]]:
    return [words[i:i + size] for i in range(0, len(words), size)]


# Jaune accent TikTok (ASS &HBBGGRR sans alpha pour \c).
_YELLOW = "&H00F0FF&"
_WHITE = "&HFFFFFF&"


def _render_segment(seg: List[dict]) -> str:
    """Construit le texte ASS d'un segment : fondu global + mot courant en jaune.

    Chaque mot bascule en jaune pendant qu'il est prononcé (effet karaoké léger),
    puis revient blanc. `\\fad` ajoute l'apparition/disparition douce du bloc.
    """
    seg_start = seg[0]['start']
    parts = [f"{{\\fad({FADE_MS},{FADE_MS})}}"]

    clean = [w for w in seg if w['word'].strip()]
    half = (len(clean) + 1) // 2  # break line au milieu (2 lignes max)

    for i, w in enumerate(clean):
        word = w['word'].strip().replace('{', '').replace('}', '')
        # Saut de ligne au milieu du segment pour 2 lignes centrées.
        if i == half:
            parts.append('\\N')
        # Décalages (centisecondes) relatifs au début du segment.
        on = max(0, int(round((w['start'] - seg_start) * 100)))
        off = max(on + 1, int(round((w['end'] - seg_start) * 100)))
        # \t(t1,t2,\c&col&) : transition de couleur dans l'intervalle du mot.
        sep = '' if i in (half - 1, len(clean) - 1) else ' '
        parts.append(
            f"{{\\c{_WHITE}\\t({on * 10},{off * 10},\\c{_YELLOW})"
            f"\\t({off * 10},{off * 10 + 1},\\c{_WHITE})}}{word}{sep}"
        )
    return ''.join(parts)


class WhisperSubtitler:

    def __init__(self, config):
        self.model_name = config.WHISPER_MODEL
        self.cache_dir = str(config.WHISPER_CACHE_DIR)
        self.subs_dir = Path(config.SUBS_DIR)
        self.subs_dir.mkdir(parents=True, exist_ok=True)
        self._model = None  # chargement paresseux
        # Whisper installe des hooks de cache KV SUR le modèle pendant le décodage
        # (état mutable partagé) : la transcription n'est PAS thread-safe. En
        # génération parallèle, on sérialise chargement + transcribe sous ce verrou.
        self._lock = threading.Lock()

        # Whisper invoque ffmpeg via le PATH : on y ajoute notre ffmpeg embarqué.
        ffmpeg_dir = Path(config.FFMPEG_PATH).parent
        if ffmpeg_dir.exists():
            os.environ['PATH'] = str(ffmpeg_dir) + os.pathsep + os.environ.get('PATH', '')

    def _load_model(self):
        if self._model is None:
            _ensure_std_streams()
            import whisper
            logger.info(f'Chargement du modèle Whisper "{self.model_name}"…')
            self._model = whisper.load_model(self.model_name, download_root=self.cache_dir)
            logger.info('Modèle Whisper prêt.')

    def generate(self, audio_path: str, output_stem: str,
                 card_title: Optional[str] = None) -> tuple[Optional[str], float]:
        """Transcrit l'audio et écrit un fichier .ass.

        Retourne (chemin_ass, card_end_time) où card_end_time est l'instant
        (en secondes) où le titre finit d'être prononcé (pour masquer les subs
        pendant que la carte Reddit est visible). Si pas de titre, retourne
        (chemin_ass, 0.0) et tous les subs sont inclus normalement.
        """
        try:
            _ensure_std_streams()
            # Sérialise la partie non thread-safe (chargement + inférence). L'écriture
            # du .ass ensuite est sûre (noms de fichiers uniques par partie).
            with self._lock:
                self._load_model()
                # Décodage rapide & déterministe : la narration TTS est nette, donc
                # temperature=0 (pas de ré-essais coûteux par fallback de température)
                # et sans report de contexte (évite boucles/répétitions) → plus
                # rapide sur le goulot Whisper, sans perte de qualité de sous-titres.
                result = self._model.transcribe(
                    str(audio_path), word_timestamps=True,
                    language='en', fp16=False, verbose=False,
                    temperature=0.0, condition_on_previous_text=False,
                )

            words: List[dict] = [
                {'word': w['word'], 'start': w['start'], 'end': w['end']}
                for seg in result.get('segments', [])
                for w in seg.get('words', [])
            ]
            if not words:
                logger.warning("Whisper n'a retourné aucun mot — pas de sous-titres.")
                return (None, 0.0)

            # card_end_time : durée d'affichage de la carte Reddit. Le titre est
            # NARRÉ en tête de l'audio → on cale la fin de la carte sur l'instant
            # EXACT où le titre finit d'être prononcé (synchro via les timestamps
            # Whisper). On accumule les caractères transcrits jusqu'à couvrir la
            # longueur du titre (robuste aux différences de tokenisation TTS↔Whisper).
            card_end = 0.0
            if card_title and words:
                def _alnum(s):
                    return ''.join(c for c in s.lower() if c.isalnum())
                target_len = len(_alnum(card_title))
                acc = 0
                for w in words:
                    acc += len(_alnum(w['word']))
                    if acc >= target_len:
                        card_end = min(w['end'] + 0.15, words[-1]['end'])
                        break
                else:
                    card_end = words[-1]['end']

            ass_path = self.subs_dir / f'{output_stem}.ass'
            segments = _words_to_segments(words)

            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write(_ass_header())
                for seg in segments:
                    if card_title and seg[-1]['end'] <= card_end:
                        continue
                    filtered_seg = [w for w in seg if w['end'] > card_end] if card_title else seg
                    if card_title and not filtered_seg:
                        continue
                    start_t = filtered_seg[0]['start']
                    end = _fmt_time(filtered_seg[-1]['end'] + 0.08)
                    start = _fmt_time(start_t)
                    f.write(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{_render_segment(filtered_seg)}\n')

            logger.info(f'Sous-titres générés : {ass_path.name} ({len(segments)} segments, card_end={card_end:.2f}s)')
            return (str(ass_path), card_end)

        except Exception as e:
            logger.error(f'Erreur génération sous-titres : {e}')
            return (None, 0.0)
