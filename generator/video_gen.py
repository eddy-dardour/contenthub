#!/usr/bin/env python3
"""
VideoAssembler — fusionne une narration audio avec une vidéo de fond via ffmpeg.

Pipeline :
  1. Mesure la durée de l'audio (ffprobe).
  2. Cale la durée finale entre MIN_DURATION et MAX_DURATION.
  3. Boucle la vidéo de fond, la recadre en portrait 1080x1920.
  4. Muxe l'audio par-dessus et encode en H.264 / AAC.
"""

import json
import logging
import random
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MIN_DURATION = 61.0
MAX_DURATION = 90.0
CARD_SHOW_SECONDS = 4.0
CARD_FADE_SECONDS = 0.5          # fondu de sortie APRÈS lecture du titre (court)
# La carte Reddit est centrée verticalement via overlay=(H-h)/2 (cf. assemble).

# Badges récompenses Reddit : tous les fichiers assets/icons/award_*.png (vrais
# awards Reddit). On en alterne 5 au hasard par vidéo (cf. _award_icons()).
AWARDS_PER_CARD = 5

# Musique de fond : on NORMALISE à une loudness cible (robuste quel que soit le
# niveau du fichier source) ~12 dB sous la voix (-14 LUFS) → clairement audible
# mais en dessous du TTS. Plus fiable qu'un gain linéaire aveugle.
MUSIC_LUFS = -24.0

# Accélération de la vidéo de fond (gameplay) : 1.3x → plus de mouvement/rythme.
# N'affecte QUE le fond (la durée finale reste calée sur l'audio).
BG_SPEED = 1.3

CRF_MIN, CRF_MAX = 18, 21
GAMMA_MIN, GAMMA_MAX = 0.96, 1.04
BRIGHTNESS_RANGE = (-0.02, 0.02)
CROP_SCALE_MIN, CROP_SCALE_MAX = 1.0, 1.03
NOISE_STRENGTH_MIN, NOISE_STRENGTH_MAX = 1, 4


class VideoAssembler:

    def __init__(self, config):
        self.config = config
        self.output_dir = Path(config.VIDEO_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width = config.VIDEO_WIDTH
        self.height = config.VIDEO_HEIGHT
        self.fps = config.VIDEO_FPS
        self.ffmpeg_path = config.FFMPEG_PATH
        self.ffprobe_path = self._resolve_ffprobe(config.FFMPEG_PATH)
        self._check_ffmpeg()
        self.video_encoder = self._pick_encoder()
        # Shuffled background pool for round-robin: every background is used
        # before any repeats. Rebuilt when the pool is exhausted.
        self._bg_pool: list = []
        self._bg_index: int = 0

    # ── Outils ───────────────────────────────────────────────────────

    def _resolve_ffprobe(self, ffmpeg_path: str) -> str:
        """ffprobe vit à côté de ffmpeg dans le même bin/."""
        p = Path(ffmpeg_path)
        if p.name.lower().startswith('ffmpeg'):
            candidate = p.with_name(p.name.lower().replace('ffmpeg', 'ffprobe'))
            if candidate.exists():
                return str(candidate)
        return 'ffprobe'

    def _check_ffmpeg(self):
        try:
            subprocess.run([self.ffmpeg_path, '-version'],
                           capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                f'ffmpeg introuvable à "{self.ffmpeg_path}". '
                'Définissez FFMPEG_PATH dans .env.'
            )

    def _pick_encoder(self) -> str:
        """Choisit le meilleur encodeur H.264 disponible dans ce build ffmpeg."""
        try:
            out = subprocess.run([self.ffmpeg_path, '-hide_banner', '-encoders'],
                                 capture_output=True, text=True).stdout
        except Exception:
            out = ''
        for enc in ('libx264', 'libopenh264', 'h264_nvenc', 'h264_qsv'):
            if enc in out:
                logger.info(f'Encodeur vidéo : {enc}')
                return enc
        logger.warning('Aucun encodeur H.264 détecté — repli sur mpeg4.')
        return 'mpeg4'

    def _randomize_encoding_params(self) -> tuple:
        crf = str(random.randint(CRF_MIN, CRF_MAX))
        # Presets rapides (~4-5× plus vite que 'slow') : on garde une variation
        # pour l'anti-fingerprint sans payer le coût d'encodage de 'slow'/'medium'.
        # La qualité reste pilotée par le CRF (18-21), inchangé.
        preset = random.choice(['veryfast', 'faster', 'fast'])
        audio_bitrate = random.choice(['192k', '256k', '320k'])
        return crf, preset, audio_bitrate

    def _invisible_fingerprint_filters(self) -> str:
        noise_seed = random.randint(1, 99999)
        noise_strength = random.randint(NOISE_STRENGTH_MIN, NOISE_STRENGTH_MAX)
        noise = f'noise=c0s={noise_strength}:c0f=t:all_seed={noise_seed}'

        brightness = random.uniform(*BRIGHTNESS_RANGE)
        contrast = random.uniform(0.95, 1.05)
        saturation = random.uniform(0.95, 1.05)
        gamma = random.uniform(GAMMA_MIN, GAMMA_MAX)
        eq = (f'eq=brightness={brightness:.4f}:contrast={contrast:.4f}'
              f':saturation={saturation:.4f}:gamma={gamma:.4f}')
        # Micro-rotation de teinte (±2°) : imperceptible à l'œil mais déplace
        # chaque pixel couleur → casse l'empreinte sans changer le rendu perçu.
        hue = random.uniform(-2.0, 2.0)
        return f'{noise},{eq},hue=h={hue:.2f}'

    def probe_duration(self, media_path) -> Optional[float]:
        """Durée (s) d'un média via ffprobe — public (utilisé par main.py pour
        écarter une narration trop courte)."""
        return self._probe_duration(Path(media_path))

    def _probe_duration(self, media_path: Path) -> Optional[float]:
        try:
            out = subprocess.run(
                [self.ffprobe_path, '-v', 'error', '-print_format', 'json',
                 '-show_format', str(media_path)],
                capture_output=True, check=True, text=True,
            )
            return float(json.loads(out.stdout)['format']['duration'])
        except Exception as e:
            logger.warning(f'ffprobe échoué sur {media_path} : {e}')
            return None

    @staticmethod
    def _esc_filter_path(path: str) -> str:
        """Échappe un chemin Windows pour usage dans un filtre ffmpeg."""
        p = str(path).replace('\\', '/')
        p = p.replace(':', '\\:')
        return p

    # ── Carte Reddit (début de vidéo) ────────────────────────────────

    @staticmethod
    def _wrap_text(draw, text: str, font, max_width: int) -> list:
        """Découpe `text` en lignes qui tiennent dans `max_width` (px)."""
        words = text.split()
        lines, current = [], ''
        for word in words:
            trial = f'{current} {word}'.strip()
            if draw.textlength(trial, font=font) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _fmt_count(n: int) -> str:
        """Formate un compteur façon réseau social : 1234 → '1.2K'."""
        return f'{n / 1000:.1f}K' if n >= 1000 else str(n)

    def _load_icon(self, name: str, size: int):
        """Charge une icône PNG (assets/icons) redimensionnée, ou None si absente."""
        try:
            from PIL import Image
            p = Path(self.config.ICONS_DIR) / name
            if not p.exists():
                return None
            return Image.open(p).convert('RGBA').resize((size, size), Image.LANCZOS)
        except Exception:
            return None

    def _award_icons(self) -> list:
        """Liste (mise en cache) de tous les badges award_*.png disponibles."""
        if getattr(self, '_awards_cache', None) is None:
            try:
                self._awards_cache = sorted(
                    p.name for p in Path(self.config.ICONS_DIR).glob('award_*.png'))
            except Exception:
                self._awards_cache = []
        return self._awards_cache

    def _make_reddit_card(self, title: str, subreddit: str,
                          out_path: Path) -> Optional[Path]:
        """Génère un PNG RGBA "post Reddit" (carte blanche, coins ronds) contenant
        r/subreddit + le titre. Renvoie le chemin, ou None en cas d'échec.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter

            W = self.width                  # 1080
            margin = 60                     # marge canvas → carte
            card_x0, card_x1 = margin, W - margin
            card_w = card_x1 - card_x0
            pad = 48                        # padding interne
            radius = 36
            avatar = 56                     # diamètre de la pastille r/

            # Toute la carte en Montserrat (sans-serif propre, proche du vrai
            # rendu Reddit). Les sous-titres TTS, eux, restent en KomikaAxis.
            montserrat = str(Path(self.config.FONTS_DIR) / 'Montserrat-Bold.ttf')
            try:
                f_meta = ImageFont.truetype(montserrat, 36)
                f_stat = ImageFont.truetype(montserrat, 36)
                f_title = ImageFont.truetype(montserrat, 50)
            except Exception:
                f_meta = f_stat = f_title = ImageFont.load_default()

            # Mesure : on a besoin d'un draw temporaire pour wrapper le titre.
            probe = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
            avail = card_w - 2 * pad
            title = (title or '').strip() or 'Reddit'
            lines = self._wrap_text(probe, title, f_title, avail)[:4]  # cap 4 lignes

            t_ascent, t_descent = f_title.getmetrics()
            title_lh = t_ascent + t_descent + 8
            header_h = max(avatar, sum(f_meta.getmetrics()))
            gap = 28
            icon_sz = 46                    # taille des icônes like/commentaire
            stats_gap = 52                  # espace titre → barre d'engagement (aéré)
            award_sz = 44                   # taille des badges récompenses
            award_gap = 52                  # espace badges → titre (aéré, titre centré)
            card_h = (pad + header_h + gap + award_sz + award_gap
                      + title_lh * len(lines)
                      + stats_gap + icon_sz + pad)
            shadow_pad = 24                 # marge pour l'ombre portée

            canvas_h = card_h + shadow_pad * 2
            img = Image.new('RGBA', (W, canvas_h), (0, 0, 0, 0))

            # Ombre portée douce (rectangle gris flouté).
            shadow = Image.new('RGBA', (W, canvas_h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            sd.rounded_rectangle(
                [card_x0, shadow_pad + 6, card_x1, shadow_pad + card_h + 6],
                radius=radius, fill=(0, 0, 0, 70))
            shadow = shadow.filter(ImageFilter.GaussianBlur(12))
            img = Image.alpha_composite(img, shadow)

            draw = ImageDraw.Draw(img)
            top = shadow_pad
            # Carte blanche.
            draw.rounded_rectangle(
                [card_x0, top, card_x1, top + card_h],
                radius=radius, fill=(255, 255, 255, 255))

            # En-tête : avatar = vrai logo Reddit (Snoo officiel) masqué en cercle.
            ax0 = card_x0 + pad
            ay0 = top + pad
            logo = self._load_icon('reddit.png', avatar)
            if logo is not None:
                mask = Image.new('L', (avatar, avatar), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, avatar - 1, avatar - 1], fill=255)
                img.paste(logo, (int(ax0), int(ay0)), mask)
            else:
                draw.ellipse([ax0, ay0, ax0 + avatar, ay0 + avatar],
                             fill=(255, 69, 0, 255))
            sub = (subreddit or 'reddit').strip()
            sub_text = f'r/{sub}'
            m_ascent, m_descent = f_meta.getmetrics()
            meta_y = ay0 + (avatar - (m_ascent + m_descent)) // 2
            draw.text((ax0 + avatar + 24, meta_y), sub_text,
                      font=f_meta, fill=(60, 60, 60, 255))

            # Badges récompenses Reddit (au-dessus du titre) + compteur gris.
            # Social proof : un post primé paraît plus crédible/viral. On affiche
            # un sous-ensemble ALÉATOIRE des vrais awards (variété entre vidéos).
            aw_y = ay0 + header_h + gap
            ax = ax0
            pool = self._award_icons()
            badges = random.sample(pool, k=min(AWARDS_PER_CARD, len(pool)))
            for badge in badges:
                bicon = self._load_icon(badge, award_sz)
                if bicon is not None:
                    img.paste(bicon, (int(ax), int(aw_y)), bicon)
                ax += award_sz + 12
            awards_count = random.randint(5, 180)
            draw.text((ax + 12, aw_y + award_sz / 2), f'{awards_count} Awards',
                      font=f_meta, fill=(120, 120, 120, 255), anchor='lm')

            # Titre (noir, multi-lignes, centré horizontalement).
            cx = card_x0 + card_w // 2
            ty = aw_y + award_sz + award_gap
            for ln in lines:
                draw.text((cx, ty), ln, font=f_title, fill=(15, 15, 15, 255),
                          anchor='mm')
                ty += title_lh

            # Barre d'engagement sous le titre : like + commentaire + partage avec
            # de fausses stats (social proof, ex. "1.2K") — icônes libres de droit.
            # `ty` pointe déjà sous la dernière ligne de titre.
            stats_y = ty + stats_gap
            x = ax0
            for name, count in (('like.png', random.randint(1200, 49000)),
                                ('comment.png', random.randint(80, 3800)),
                                ('share.png', random.randint(150, 6500))):
                icon = self._load_icon(name, icon_sz)
                if icon is not None:
                    img.paste(icon, (int(x), int(stats_y)), icon)
                txt = self._fmt_count(count)
                x += icon_sz + 18
                draw.text((x, stats_y + icon_sz / 2), txt, font=f_stat,
                          fill=(120, 120, 120, 255), anchor='lm')
                x += draw.textlength(txt, font=f_stat) + 64

            img.save(str(out_path))
            return out_path
        except Exception as e:
            logger.warning(f'Génération carte Reddit échouée : {e}')
            return None

    # ── Assemblage ───────────────────────────────────────────────────

    def assemble(self, audio_path: str, output_filename: str,
                 ass_path: Optional[str] = None,
                 reddit_title: Optional[str] = None,
                 subreddit: Optional[str] = None,
                 card_end_time: float = 0.0) -> Optional[str]:
        audio_path = Path(audio_path)
        output_path = self.output_dir / f'{output_filename}.mp4'

        audio_dur = self._probe_duration(audio_path)
        if audio_dur is None:
            logger.error('Impossible de mesurer la durée audio — abandon.')
            return None

        # Carte Reddit (titre dans un cadre blanc) affichée en début de vidéo.
        # Générée uniquement si un titre est fourni (= partie 1 d'une histoire).
        card_path = None
        if reddit_title:
            card_file = Path(self.config.SUBS_DIR) / f'{output_filename}_card.png'
            card_path = self._make_reddit_card(reddit_title, subreddit or 'reddit',
                                               card_file)

        # Round-robin through backgrounds: shuffle the full pool, then step
        # through it one by one. Reshuffle when exhausted so no repeats until
        # every background has been used at least once.
        if self._bg_index >= len(self._bg_pool):
            pool = self.config.background_pool()
            if not pool:
                raise FileNotFoundError(
                    f'No background videos in {self.config.ASSETS_DIR}.')
            self._bg_pool = pool.copy()
            random.shuffle(self._bg_pool)
            self._bg_index = 0
        background = self._bg_pool[self._bg_index]
        self._bg_index += 1
        logger.info(f'Background: {background.name} ({self._bg_index}/{len(self._bg_pool)})')

        # Durée cible : on NE comble JAMAIS au silence. La cible ne dépasse pas la
        # durée réelle de la narration (plafonnée à MAX_DURATION). Les histoires
        # trop courtes (< MIN_DURATION) sont écartées en amont (main.py) → on change
        # d'histoire plutôt que d'ajouter du blanc.
        target = min(audio_dur, MAX_DURATION)
        mode = 'coupée' if target < audio_dur else 'exacte'
        logger.info(f'Durée audio={audio_dur:.1f}s -> cible={target:.1f}s ({mode})')

        over = random.uniform(CROP_SCALE_MIN, CROP_SCALE_MAX)
        scale_w = int(self.width * over) // 2 * 2    # pair (requis yuv420p)
        scale_h = int(self.height * over) // 2 * 2
        vf = (
            f'scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={self.width}:{self.height},'
            # Accélère le fond ×BG_SPEED (setpts avant fps → rééchantillonné proprement
            # à {self.fps} fps). Le fond est bouclé à l'infini puis coupé à `target`,
            # donc seule la vitesse du mouvement change, pas la durée finale.
            f'setpts=PTS/{BG_SPEED},'
            f'fps={self.fps},setsar=1'
        )

        # Filtres anti-fingerprint INVISIBLES (bruit luma faible + jitter couleur).
        # Rend chaque vidéo unique au niveau pixel sans dégrader la qualité perçue.
        vf += f',{self._invisible_fingerprint_filters()}'

        # Sous-titres ASS (incrustés sur toute la vidéo).
        # fontsdir indique à libass où trouver "Montserrat Black" (sans install système).
        subs_filter = ''
        if ass_path and Path(ass_path).exists():
            fonts_dir = self._esc_filter_path(self.config.FONTS_DIR)
            subs_filter = (
                f",ass='{self._esc_filter_path(ass_path)}'"
                f":fontsdir='{fonts_dir}'"
            )

        # Chaîne vidéo : crop portrait → [carte Reddit] → sous-titres → trim.
        if card_path:
            # La carte reste PLEINEMENT visible pendant toute la lecture du titre
            # (`hold` = card_end_time, instant où le titre est fini d'être prononcé),
            # PUIS fond de sortie court. Les sous-titres du corps commencent à `hold`.
            hold = card_end_time if card_end_time > 0 else CARD_SHOW_SECONDS
            card_total = hold + CARD_FADE_SECONDS
            subs_no_comma = subs_filter[1:] if subs_filter else ''
            subs_prefix = f'{subs_no_comma},' if subs_no_comma else ''
            vchain = (
                f'[0:v]{vf}[bg];'
                f'[2:v]format=rgba,'
                f'fade=t=in:st=0:d=0.3:alpha=1,'
                f'fade=t=out:st={hold:.2f}:d={CARD_FADE_SECONDS:.2f}:alpha=1[card];'
                f"[bg][card]overlay=(W-w)/2:(H-h)/2"
                f":enable='between(t,0,{card_total:.2f})'[ov];"
                f'[ov]{subs_prefix}'
                f'trim=0:{target:.3f},setpts=PTS-STARTPTS[v]'
            )
        else:
            vchain = (
                f'[0:v]{vf}{subs_filter},'
                f'trim=0:{target:.3f},setpts=PTS-STARTPTS[v]'
            )

        # Filtre audio. Objectifs : (1) durée exacte, (2) qualité perçue pro,
        # (3) atténuer le côté "robotique" de la voix IA.
        #   - léger EQ : coupe les sub-basses (highpass) + chaleur médiums + air aigus
        #     → voix moins synthétique, plus naturelle.
        #   - acompressor doux : dynamique régulière (rendu "broadcast").
        #   - loudnorm I=-14 : cible de loudness TikTok → ni trop faible (perçu
        #     bas de gamme) ni écrêté. Micro-jitter aléatoire sur les fréquences
        #     d'EQ pour varier l'empreinte audio d'une vidéo à l'autre.
        warm_freq = random.randint(180, 240)
        air_freq = random.randint(9000, 11000)
        a_filter = (
            f'atrim=0:{target:.3f},'
            'highpass=f=70,'
            f'equalizer=f={warm_freq}:t=q:w=1.0:g=1.6,'   # chaleur médium-bas (corps de voix)
            f'equalizer=f={air_freq}:t=q:w=2.0:g=0.6,'    # air discret : aigus moins durs/numériques
            # Compression DOUCE (ratio bas, attaque lente) : préserve le naturel et
            # les transitoires de la voix → rendu moins "robotique"/écrasé.
            'acompressor=threshold=-20dB:ratio=2.2:attack=20:release=160,'
            'loudnorm=I=-14:TP=-1.5:LRA=11,'
            'asetpts=N/SR/TB'
        )

        # Réglages qualité selon l'encodeur (libx264 = crf, les autres = bitrate).
        crf, preset, audio_bitrate = self._randomize_encoding_params()
        # Intervalle de keyframes (GOP) aléatoire : varie la structure du bitstream
        # d'une vidéo à l'autre (anti-fingerprint) sans aucun impact visuel.
        gop = str(random.randint(48, 120))
        if self.video_encoder == 'libx264':
            # profile high + level 4.0 = exactement ce que produit un smartphone.
            quality_args = ['-preset', preset, '-crf', crf,
                            '-profile:v', 'high', '-level', '4.0', '-g', gop]
        else:
            quality_args = ['-b:v', '5000k', '-g', gop]

        cmd = [
            self.ffmpeg_path, '-y',
            '-stream_loop', '-1', '-i', str(background),                  # input 0 : fond bouclé
            '-i', str(audio_path),                                        # input 1 : narration
        ]
        next_input = 2
        if card_path:
            hold = card_end_time if card_end_time > 0 else CARD_SHOW_SECONDS
            card_total = hold + CARD_FADE_SECONDS
            # input 2 : carte Reddit (image bouclée pour toute sa durée d'affichage
            # = lecture du titre + fondu de sortie).
            cmd += ['-loop', '1', '-framerate', str(self.fps),
                    '-t', f'{card_total:.2f}', '-i', str(card_path)]
            next_input += 1

        # Chaîne audio : narration (toujours) + ping en intro + musique de fond
        # discrète. On amixe sans normaliser (normalize=0) pour garder la narration
        # à pleine voix ; durée calée sur la narration (duration=first).
        pre = f'[1:a]{a_filter}[narr];'
        mix_labels = ['[narr]']

        # Ping (attire l'attention) mixé à t=0 sans décaler la narration.
        ping_path = getattr(self.config, 'PING_SOUND', None)
        if ping_path and Path(ping_path).exists():
            cmd += ['-i', str(ping_path)]
            pre += (f'[{next_input}:a]aresample=async=1,adelay=0|0,'
                    f'volume=0.9[ping];')
            mix_labels.append('[ping]')
            next_input += 1

        # Musique de fond : normalisée à MUSIC_LUFS (audible sous la voix),
        # coupée à la durée et fondue en entrée/sortie.
        music_path = getattr(self.config, 'MUSIC_SOUND', None)
        if music_path and Path(music_path).exists():
            cmd += ['-i', str(music_path)]
            fade_out = max(target - 2.0, 0.1)
            pre += (f'[{next_input}:a]atrim=0:{target:.3f},'
                    f'loudnorm=I={MUSIC_LUFS}:TP=-2:LRA=11,'
                    f'afade=t=in:st=0:d=1,'
                    f'afade=t=out:st={fade_out:.2f}:d=2[music];')
            mix_labels.append('[music]')
            next_input += 1

        if len(mix_labels) > 1:
            # alimiter final : garde-fou anti-saturation quand voix + ping + musique
            # se cumulent (amix normalize=0 somme les pistes).
            audio_chain = (pre + ''.join(mix_labels)
                           + f'amix=inputs={len(mix_labels)}:duration=first:'
                           f'dropout_transition=0:normalize=0,'
                           f'alimiter=limit=0.97[a]')
        else:
            audio_chain = f'[1:a]{a_filter}[a]'

        cmd += [
            '-filter_complex',
            f'{vchain};{audio_chain}',
            '-map', '[v]', '-map', '[a]',
            '-t', f'{target:.3f}',                                        # garde-fou durée finale
            '-c:v', self.video_encoder, *quality_args,
            '-pix_fmt', 'yuv420p',
            # Tags couleur broadcast standard (bt709) : ce que tague un vrai
            # appareil photo/téléphone. Une vidéo non taguée peut paraître "traitée".
            '-colorspace', 'bt709', '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-c:a', 'aac', '-b:a', audio_bitrate,
            # Hygiène métadonnées : EFFACE toute trace de la source/pipeline.
            #   -map_metadata -1 : supprime les métadonnées héritées de la source.
            #   +bitexact (conteneur + flux) : supprime le tag encodeur "Lavf..."
            #     ET la chaîne de version x264 (SEI) → aucune signature ffmpeg/bot.
            #   TikTok ré-encode de toute façon à l'upload : un fichier sans
            #     métadonnée est le plus neutre possible.
            '-map_metadata', '-1',
            '-fflags', '+bitexact',
            '-flags:v', '+bitexact',
            '-flags:a', '+bitexact',
            '-movflags', '+faststart',
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f'ffmpeg a échoué :\n{result.stderr[-1500:]}')
                return None

            final_dur = self._probe_duration(output_path)
            logger.info(
                f'Vidéo assemblée : {output_path.name} '
                f'({final_dur:.1f}s)' if final_dur else f'Vidéo assemblée : {output_path.name}'
            )
            return str(output_path)

        except Exception as e:
            logger.error(f'Erreur assemblage vidéo : {e}')
            return None
        finally:
            # Nettoie le PNG temporaire de la carte Reddit.
            if card_path:
                try:
                    Path(card_path).unlink(missing_ok=True)
                except Exception:
                    pass
