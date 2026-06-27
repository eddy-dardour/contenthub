"""Catalogue modulaire des types de contenu.

Remplace l'ancien concept de « bundle » (une histoire déclinée sur tous les
réseaux) par un catalogue extensible : chaque entrée décrit UN type de contenu
publiable, ce qu'il génère, et sur quelles plateformes il est autorisé.

Ajouter un type = ajouter un ContentType à CATALOG. Aucune autre modification du
cœur n'est nécessaire. Les types pourront plus tard être spécifiques à certaines
plateformes (cf. champ `networks`).

Pour l'instant un seul générateur réel existe : l'outil local « manual » (vidéo
verticale TTS). D'autres générateurs (images, carrousels…) viendront brancher
leur propre `generator_kind`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Générateurs disponibles. Pour l'instant seul "manual" (outil TTS local) existe.
GEN_MANUAL = "manual"


@dataclass(frozen=True)
class ContentType:
    """Un type de contenu du catalogue.

    id            : clé stable (ex: "tts_minecraft").
    label         : nom affiché.
    description   : phrase courte pour l'UI.
    icon          : emoji/glyphe affiché sur la carte.
    generator_kind: quel générateur produit ce contenu (GEN_MANUAL pour l'instant).
    gen_type      : argument --type passé à l'outil de génération (drama/facts/rotate…).
    networks      : plateformes AUTORISÉES pour ce type. Le contenu n'est distribué
                    que vers ces réseaux (et uniquement leurs comptes liés+actifs).
    """
    id: str
    label: str
    description: str
    icon: str = "🎬"
    generator_kind: str = GEN_MANUAL
    gen_type: str = "rotate"
    networks: tuple[str, ...] = ()


# ── Catalogue ────────────────────────────────────────────────────────────────
# Premier type : vidéo TTS façon « Minecraft parkour » (histoire Reddit lue en
# voix off sur gameplay), épinglée à TikTok + YouTube + Facebook.
CATALOG: tuple[ContentType, ...] = (
    ContentType(
        id="tts_drama",
        label="TTS Drama",
        description="Histoire drama Reddit en voix off sur gameplay Minecraft vertical (TTS + sous-titres).",
        icon="🎭",
        generator_kind=GEN_MANUAL,
        gen_type="drama",
        networks=("tiktok", "youtube", "facebook"),
    ),
)


def list_types() -> tuple[ContentType, ...]:
    return CATALOG


def get_type(type_id: str) -> ContentType | None:
    for ct in CATALOG:
        if ct.id == type_id:
            return ct
    return None
