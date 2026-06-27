#!/usr/bin/env python3
"""
Découpage des longues histoires en parties (Part 1, Part 2…).

On découpe le TEXTE aux frontières de phrases avant la synthèse vocale,
plutôt que l'audio après coup — c'est déterministe et propre.
"""

import re
from typing import List

# Vitesse de parole edge-tts avec accélération +20% (mots/minute réels).
DEFAULT_WPM = 156

# Mots max par partie : ~210 mots ≈ 80s à 156 wpm → format court 60-90s.
DEFAULT_MAX_WORDS = 210

# Mots min par partie : ~63s de TTS à 156 wpm (> seuil TikTok 60s). Une dernière
# partie plus courte est fusionnée avec la précédente pour qu'aucune vidéo ne
# descende sous la minute (et donc qu'on n'ait jamais à combler au silence).
DEFAULT_MIN_WORDS = 165

_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')


def estimate_duration_seconds(script: str, wpm: int = DEFAULT_WPM) -> float:
    """Estime la durée TTS d'un script d'après son nombre de mots."""
    words = len(script.split())
    if words == 0:
        return 0.0
    return words / wpm * 60.0


def split_script(script: str, max_words_per_part: int = DEFAULT_MAX_WORDS,
                 min_words_per_part: int = DEFAULT_MIN_WORDS) -> List[str]:
    """Découpe un script en parties aux frontières de phrases.

    Chaque partie vise <= max_words_per_part mots, sans jamais couper
    une phrase en deux. Si la dernière partie tombe sous min_words_per_part
    (< ~1min de TTS), elle est fusionnée avec la précédente pour garantir
    une durée minimale par vidéo. Retourne au moins une partie.
    """
    script = script.strip()
    if not script:
        return ['']

    sentences = _SENTENCE_RE.split(script)
    parts: List[str] = []
    current: List[str] = []
    count = 0

    for sentence in sentences:
        wc = len(sentence.split())
        # Si ajouter cette phrase dépasse la limite et qu'on a déjà du contenu,
        # on clôt la partie courante.
        if count + wc > max_words_per_part and current:
            parts.append(' '.join(current))
            current, count = [], 0
        current.append(sentence)
        count += wc

    if current:
        parts.append(' '.join(current))

    # Dernière partie trop courte (< ~1min) : on la fusionne avec la précédente,
    # PUIS on rééquilibre le bloc en deux parties aux frontières de phrases. Une
    # fusion brute donnerait une partie trop longue, coupée à l'encodage (perte de
    # narration) ; l'équilibrage garantit deux parties ni trop courtes ni trop longues.
    if len(parts) >= 2 and len(parts[-1].split()) < min_words_per_part:
        tail = parts.pop()
        combined = parts.pop() + ' ' + tail  # ordre préservé : précédente puis queue
        sents = _SENTENCE_RE.split(combined)
        # On ne rééquilibre en deux que si le bloc peut donner DEUX parties viables
        # (chacune ≥ min) ; sinon une seule partie — éviter deux vidéos < 1min qui
        # seraient toutes deux écartées.
        if len(sents) >= 2 and len(combined.split()) >= 2 * min_words_per_part:
            target = len(combined.split()) // 2
            acc, cut = 0, None
            for i, s in enumerate(sents):
                acc += len(s.split())
                if acc >= target and cut is None:
                    cut = i + 1
                    break
            cut = cut or max(1, len(sents) // 2)
            cut = min(cut, len(sents) - 1)
            parts.append(' '.join(sents[:cut]))
            parts.append(' '.join(sents[cut:]))
        else:
            parts.append(combined)

    return parts if parts else [script]
