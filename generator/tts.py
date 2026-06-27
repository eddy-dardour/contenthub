#!/usr/bin/env python3
"""
Text-to-Speech — edge-tts (primary, free) + gtts (fallback, free)
"""

import asyncio
import logging
import random
import re
import sys
from pathlib import Path
from typing import Optional

# Fix asyncio on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)

# Voix edge-tts (masculine, féminine). On alterne H/F selon l'index vidéo :
# vidéo 1 = homme, vidéo 2 = femme, vidéo 3 = homme…
_MALE_VOICE, _FEMALE_VOICE = 'en-US-ChristopherNeural', 'en-US-JennyNeural'


def _normalize_rate(raw) -> str:
    """Normalise une saisie utilisateur en format edge-tts ("+15%").

    Accepte : "+15%", "15%", "15", "+15", "-10", "1.15" (→ "+15%"), vide → "+0%".
    """
    s = str(raw or '').strip().replace(' ', '')
    if not s:
        return '+0%'
    # Facteur multiplicatif (ex: "1.15" → +15%).
    try:
        if '.' in s and '%' not in s:
            pct = round((float(s) - 1) * 100)
            return f'{pct:+d}%'
    except ValueError:
        pass
    m = re.match(r'^([+-]?)(\d+)%?$', s)
    if not m:
        logger.warning(f'Vitesse voix invalide "{raw}" — repli sur +0%')
        return '+0%'
    sign, num = m.group(1) or '+', m.group(2)
    return f'{sign}{num}%'


def _normalize_pitch(raw) -> str:
    """Normalise une hauteur en format edge-tts ("+8Hz"). Vide → "+0Hz"."""
    s = str(raw or '').strip().replace(' ', '')
    m = re.match(r'^([+-]?)(\d+)\s*[hH]?[zZ]?$', s)
    if not m:
        return '+0Hz'
    sign, num = m.group(1) or '+', m.group(2)
    return f'{sign}{num}Hz'


class TextToSpeech:

    def __init__(self, config):
        self.output_dir = Path(config.AUDIO_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tts_engine = config.TTS_ENGINE
        self.edge_voice = config.EDGE_TTS_VOICE
        self.language = config.TTS_LANGUAGE
        self.rate = _normalize_rate(getattr(config, 'TTS_RATE', '+0%'))
        self.pitch = _normalize_pitch(getattr(config, 'TTS_PITCH', '+0Hz'))
        # Paire (homme, femme) fixe ; voice_for_index() alterne selon l'index vidéo.
        self._male, self._female = _MALE_VOICE, _FEMALE_VOICE
        logger.info(f'TTS engine: {self.tts_engine} | voice: {self.edge_voice} | '
                    f'rate: {self.rate} | pitch: {self.pitch} | '
                    f'alternance: {self._male}/{self._female}')

    def voice_for_index(self, n: int) -> str:
        """Voix alternée selon l'index vidéo (0-based) : pair = homme, impair = femme."""
        return self._male if n % 2 == 0 else self._female

    def generate_edge_tts(self, text: str, output_filename: str,
                          voice: Optional[str] = None) -> Optional[str]:
        try:
            import aiohttp
            import edge_tts

            output_path = self.output_dir / f'tts_{output_filename}.mp3'

            effective_rate = self._jitter_rate(self.rate)
            effective_pitch = self._jitter_pitch(self.pitch)
            v = voice or self.edge_voice

            async def _run():
                # Si aiodns/c-ares est installé, aiohttp l'utilise par défaut pour
                # la résolution DNS — et son résolveur échoue dans certains
                # environnements réseau (VPN, DNS custom) avec "Could not contact
                # DNS servers", alors que le DNS système fonctionne. On force donc
                # le résolveur natif de Python (ThreadedResolver), fiable partout.
                connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
                communicate = edge_tts.Communicate(
                    text, v, rate=effective_rate, pitch=effective_pitch,
                    connector=connector)
                await communicate.save(str(output_path))

            asyncio.run(_run())
            logger.info(f'edge-tts audio saved: {output_path} (voice: {v})')
            return str(output_path)
        except Exception as e:
            logger.error(f'edge-tts error: {e}')
            return None

    @staticmethod
    def _jitter_rate(base_rate: str) -> str:
        """Ajoute un léger jitter au rate pour éviter un fingerprint TTS identique.
        ±1% de variation pour vitesse sans perte de qualité.
        """
        m = re.match(r'^([+-]?)(\d+)%$', base_rate)
        if not m:
            return base_rate
        sign_char, num_str = m.group(1) or '+', m.group(2)
        base_pct = int(num_str) if sign_char == '+' else -int(num_str)
        jitter = random.randint(-1, 1)
        new_pct = base_pct + jitter
        return f'{new_pct:+d}%'

    @staticmethod
    def _jitter_pitch(base_pitch: str) -> str:
        """Léger jitter de hauteur (±2 Hz) : varie l'empreinte audio sans
        altération perceptible."""
        m = re.match(r'^([+-]?)(\d+)Hz$', base_pitch)
        if not m:
            return base_pitch
        sign_char, num_str = m.group(1) or '+', m.group(2)
        base_hz = int(num_str) if sign_char == '+' else -int(num_str)
        new_hz = base_hz + random.randint(-2, 2)
        return f'{new_hz:+d}Hz'

    def generate_google_tts(self, text: str, output_filename: str) -> Optional[str]:
        try:
            from gtts import gTTS

            output_path = self.output_dir / f'tts_{output_filename}.mp3'
            tts = gTTS(text=text, lang=self.language, slow=False)
            tts.save(str(output_path))
            logger.info(f'gtts audio saved: {output_path}')
            return str(output_path)
        except Exception as e:
            logger.error(f'gtts error: {e}')
            return None

    def generate_tts(self, text: str, output_filename: str,
                     voice: Optional[str] = None) -> Optional[str]:
        if len(text) > 5000:
            text = text[:5000]
            logger.warning('Text truncated to 5000 chars')

        if self.tts_engine == 'edge':
            result = self.generate_edge_tts(text, output_filename, voice=voice)
            if result is None:
                logger.warning('edge-tts failed, falling back to gtts')
                result = self.generate_google_tts(text, output_filename)
            return result

        if self.tts_engine == 'google':
            return self.generate_google_tts(text, output_filename)

        logger.error(f'Unknown TTS engine: {self.tts_engine}')
        return None
