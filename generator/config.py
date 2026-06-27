#!/usr/bin/env python3
"""
Configuration — fonctionne aussi bien en script qu'en exécutable PyInstaller.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# ── Résolution des chemins (script vs exe PyInstaller) ───────────────

def _app_dir() -> Path:
    """Dossier où vivent les ressources modifiables (.env, assets/, output/).

    - En exe : le dossier qui contient le .exe.
    - En script : le dossier du projet.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _bundle_dir() -> Path:
    """Dossier des ressources embarquées dans le bundle PyInstaller (_MEIPASS)."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', _app_dir()))
    return Path(__file__).parent


APP_DIR = _app_dir()
BUNDLE_DIR = _bundle_dir()

# Charge .env à côté de l'exe (créé au premier lancement s'il manque).
# encoding utf-8-sig : tolère un éventuel BOM (Set-Content -Encoding utf8 en ajoute un).
ENV_PATH = APP_DIR / '.env'
load_dotenv(ENV_PATH, encoding='utf-8-sig')


def _resolve_ffmpeg() -> str:
    """ffmpeg embarqué en priorité, sinon variable d'env, sinon PATH."""
    bundled = BUNDLE_DIR / 'ffmpeg' / 'ffmpeg.exe'
    if bundled.exists():
        return str(bundled)
    env_val = os.getenv('FFMPEG_PATH')
    if env_val and Path(env_val).exists():
        return env_val
    return 'ffmpeg'

def _resolve_asset_subdir(name: str) -> Path:
    """Resolve asset subdirectory: try bundle first, fallback to app dir."""
    bundled = BUNDLE_DIR / 'assets' / name
    if bundled.exists():
        return bundled
    return APP_DIR / 'assets' / name


class Config:
    APP_DIR = APP_DIR
    BUNDLE_DIR = BUNDLE_DIR

    # Surcharge possible par l'appelant (la plateforme ContentHub lance ce script
    # en sous-processus) : pointe assets/, output/ et models/ vers contenthub/.
    _ASSETS_OVERRIDE = os.getenv('TIKTOK_ASSETS_DIR')
    _OUTPUT_OVERRIDE = os.getenv('TIKTOK_OUTPUT_DIR')

    # Sorties : à côté de l'exe, ou surchargées par TIKTOK_OUTPUT_DIR.
    OUTPUT_DIR = Path(_OUTPUT_OVERRIDE) if _OUTPUT_OVERRIDE else APP_DIR / 'output'
    AUDIO_DIR = OUTPUT_DIR / 'audio'
    VIDEO_DIR = OUTPUT_DIR / 'videos'
    SUBS_DIR = OUTPUT_DIR / 'subs'
    LOG_DIR = OUTPUT_DIR / 'logs'

    # Cache des modèles Whisper. Surchargeable (resources/models partagé) via
    # WHISPER_CACHE_DIR pour éviter de re-télécharger le modèle.
    WHISPER_CACHE_DIR = (Path(os.getenv('WHISPER_CACHE_DIR'))
                         if os.getenv('WHISPER_CACHE_DIR') else APP_DIR / 'models')

    for directory in [OUTPUT_DIR, AUDIO_DIR, VIDEO_DIR, SUBS_DIR, LOG_DIR,
                      WHISPER_CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    # TTS
    TTS_ENGINE = os.getenv('TTS_ENGINE', 'edge')
    TTS_LANGUAGE = os.getenv('TTS_LANGUAGE', 'en')
    EDGE_TTS_VOICE = os.getenv('EDGE_TTS_VOICE', 'en-US-ChristopherNeural')
    # Débit de voix (style TikTok). +25% = rythmé/vif ; le pitch + filtre adouci
    # gardent un rendu naturel malgré la vitesse.
    TTS_RATE = os.getenv('TTS_RATE', '+25%')
    # Hauteur (pitch) edge-tts, format "+8Hz". Légère hausse = voix plus claire/agréable.
    TTS_PITCH = os.getenv('TTS_PITCH', '+8Hz')

    # Reddit — User-Agent HTTP pour les backends publics (aucune auth requise).
    REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'TikTokAutoBot/1.0')

    # Contenu — type pilote le subreddit et la stratégie.
    CONTENT_TYPE = os.getenv('CONTENT_TYPE', 'drama')  # drama | facts | rotate

    # Chaque type pioche dans PLUSIEURS subreddits (multi-source) pour varier les
    # histoires et maximiser le choix de posts très appréciés. `subreddit` reste
    # le sub principal (rétro-compat) ; `subreddits` est la liste complète scannée.
    CONTENT_TYPE_MAP = {
        'drama': {
            'subreddit': 'AmItheAsshole',
            'subreddits': [
                # Drama relationnel viral (le cœur)
                'AmItheAsshole',    # le classique du drama, fort engagement
                'AITAH',            # spin-off d'AITA, croissance massive
                'TwoHotTakes',      # drama relationnel, communauté en forte croissance
                'BestofRedditorUpdates',  # arcs multi-parties — très viral TikTok
                # Tromperie / infidélité
                'survivinginfidelity',  # trahison, score drama élevé
                'AsOneAfterInfidelity', # reconstruction après tromperie
                'Infidelity',       # récits de tromperie
                # Couple / dating / rupture
                'relationship_advice',  # drama relationnel, très partagé
                'relationships',    # idem, fort volume
                'dating',           # rencontres, situations gênantes
                'dating_advice',    # galères de dating
                'breakups',         # ruptures, contenu émotionnel
                # Mariage / divorce
                'Marriage',         # histoires de couple marié
                'Divorce',          # séparations, arcs dramatiques forts
                'weddingshaming',   # drama de mariage, viral
                'JUSTNOMIL',        # belle-mère toxique, extrêmement populaire
                # Amitié
                'FriendshipAdvice', # drama entre amis
                # Confessions / vie / soirées (party fails)
                'tifu',             # "today I messed up" — fails captivants
                'TrueOffMyChest',   # confessions brutes
                'confession',       # secrets et aveux
                'offmychest',       # contenu émotif lourd
                # Vengeance satisfaisante (fort engagement)
                'pettyrevenge',     # vengeances satisfaisantes
                'ProRevenge',       # vengeances épiques
                'MaliciousCompliance',  # règles suivies à la lettre, viral
                # Famille toxique
                'raisedbynarcissists',  # trauma familial, hook parasocial fort
                'EntitledParents',  # parents abusifs — rage-bait viral
                'JUSTNOFAMILY',     # famille toxique au sens large
                'JUSTNOSO',         # partenaire toxique (significant other)
                'insaneparents',    # captures de parents délirants → très viral
                # Gens odieux / clients / entitled (rage-bait fort)
                'ChoosingBeggars',  # mendiants exigeants — ultra viral
                'entitledpeople',   # gens qui se croient tout permis
                'IDontWorkHereLady',  # quiproquos absurdes en magasin
                'TalesFromTheCustomer',  # horreurs côté client
                'TalesFromRetail',  # horreurs côté vendeur
                # Vengeance hardcore
                'NuclearRevenge',   # vengeances extrêmes, satisfaction maximale
                # Travail (drama de boulot très partagé)
                'antiwork',         # conflits patron/employé, démissions épiques
                'WorkReform',       # idem, fort engagement
                # Variantes AITA + perspectives
                'AmItheJerk',       # clone AITA, histoires fraîches
                'AmItheButtface',   # variante AITA, moins saturée
                'TwoXChromosomes',  # récits perso, forte communauté
            ],
            'strategy': 'story',
        },
        'facts': {
            'subreddit': 'todayilearned',
            'subreddits': [
                'todayilearned',    # faits sourcés, le pilier
                'interestingasfuck',  # faits visuels marquants
                'Damnthatsinteresting',  # idem, fort partage
                'Showerthoughts',   # insights partageables, style "faits"
                'YouShouldKnow',    # connaissances pratiques de la vie
                'LifeProTips',      # tips "savais-tu?" pour vivre
                'explainlikeimfive',  # faits complexes rendus digestibles — format TikTok
                'AskScience',       # faits scientifiques sourcés
                'space',            # faits spatiaux fascinants
                'history',          # anecdotes historiques marquantes
                'science',          # actualités et faits scientifiques
                'Futurology',       # tech/futur, faits prospectifs
                'mildlyinteresting',  # faits visuels du quotidien, fort partage
                'AskHistorians',    # faits historiques rigoureux et sourcés
                'Awwducational',    # faits animaliers (mignons + instructifs)
                'coolguides',       # infographies → faits condensés
                'nottheonion',      # vraies actus absurdes (hook "incroyable mais vrai")
                'UpliftingNews',    # faits positifs, bon pour la rétention
                'Foodforthought',   # idées et faits qui font réfléchir
                'NoStupidQuestions',  # réponses-faits à des questions courantes
                'wikipedia',        # anecdotes encyclopédiques marquantes
            ],
            'strategy': 'facts',
        },
    }

    # Sources de RÉSERVE (stand-by). Le scraper les active dynamiquement UNIQUEMENT
    # quand les sources principales se tarissent (pool incomplet) — c.-à-d. quand
    # l'historique anti-répétition a épuisé les histoires des subs principaux. Évite
    # de scanner ces subs en temps normal (rapidité) tout en garantissant un flux
    # quasi-infini d'histoires neuves sur le long terme.
    EXTRA_SUBREDDITS = {
        'drama': [
            'AmITheDevil', 'amioverreacting', 'bridezillas', 'weddingdrama',
            'inlaws', 'MILstories', 'JustNoTalk', 'stepparents',
            'talesfromtechsupport', 'TalesFromYourServer', 'TalesFromThePharmacy',
            'KitchenConfidential', 'TalesFromTheFrontDesk', 'recruitinghell',
            'TalesFromThePizzaGuy', 'AmItheKaren', 'wedding', 'Parenting',
            'Mommit', 'CharlotteDobreYouTube',
        ],
        'facts': [
            'EverythingScience', 'biology', 'astronomy', 'geography', 'psychology',
            'philosophy', 'economics', 'technology', 'OutOfTheLoop', 'AskReddit',
            'HistoryPorn', 'DepthHub', 'AskEngineers', 'medicine', 'gadgets',
        ],
    }

    # Découpage des longues histoires en parties (Part 1, Part 2…).
    AUTO_SPLIT = os.getenv('AUTO_SPLIT', 'true').lower() == 'true'
    SPLIT_MAX_DURATION = float(os.getenv('SPLIT_MAX_DURATION', '90'))

    # Sous-titres Whisper (modèle téléchargé au 1er lancement vers models/).
    # '.en' = modèle anglais dédié : plus rapide ET plus précis que le
    # multilingue (la transcription est de toute façon forcée en anglais).
    WHISPER_MODEL = os.getenv('WHISPER_MODEL', 'base.en')  # tiny.en | base.en | small.en

    # Vidéo. CRF/preset/bitrate sont randomisés à l'encodage (anti-fingerprint)
    # directement dans video_gen.py — pas de valeur figée ici.
    VIDEO_WIDTH = 1080
    VIDEO_HEIGHT = 1920
    VIDEO_FPS = 30

    # Pool de vidéos de fond : dossier assets/ (à côté de l'exe, sinon embarqué).
    if _ASSETS_OVERRIDE:
        ASSETS_DIR = Path(_ASSETS_OVERRIDE)
    else:
        ASSETS_DIR = APP_DIR / 'assets'
        if not ASSETS_DIR.exists():
            ASSETS_DIR = BUNDLE_DIR / 'assets'

    FONTS_DIR = _resolve_asset_subdir('fonts')
    ICONS_DIR = _resolve_asset_subdir('icons')

    # Son "ping" joué en tout début de vidéo (attire l'attention). Optionnel :
    # déposer assets/ping.mp3. Absent → aucun son ajouté (rendu inchangé).
    PING_SOUND = ASSETS_DIR / 'ping.mp3'

    # Musique de fond (volume très faible) jouée sous la narration. Optionnel :
    # déposer assets/song.mp3. Absent → aucune musique (rendu inchangé).
    MUSIC_SOUND = ASSETS_DIR / 'song.mp3'

    FFMPEG_PATH = _resolve_ffmpeg()

    # TikTok
    TIKTOK_SESSION_ID = os.getenv('TIKTOK_SESSION_ID')

    # Persistance — historique des histoires déjà utilisées (IDs Reddit +
    # empreintes de titres). C'est lui qui empêche les répétitions, donc il vit
    # À CÔTÉ de l'exe et PAS dans output/ (vidé par _reset_output à chaque run).
    PROCESSED_IDS_FILE = APP_DIR / 'processed_ids.json'
    # Ancien emplacement (dans output/) — migré automatiquement au 1er chargement.
    LEGACY_PROCESSED_IDS_FILE = OUTPUT_DIR / 'processed_ids.json'

    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

    _VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}

    @classmethod
    def background_pool(cls) -> list:
        """Liste les vidéos de fond disponibles dans assets/."""
        if not cls.ASSETS_DIR.exists():
            return []
        return sorted(
            p for p in cls.ASSETS_DIR.iterdir()
            if p.suffix.lower() in cls._VIDEO_EXTS
        )

    @classmethod
    def reload(cls):
        """Relit les variables d'environnement dans les attributs de classe.

        À appeler après load_dotenv(override=True) : sans cela, les attributs
        de classe (TTS_RATE, etc.) restent figés à la valeur lue lors de
        l'import de ce module — donc une modification de .env via la GUI n'est
        jamais prise en compte sans redémarrer l'application.
        """
        cls.REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'TikTokAutoBot/1.0')
        cls.TTS_RATE = os.getenv('TTS_RATE', '+25%')
        cls.TTS_PITCH = os.getenv('TTS_PITCH', '+8Hz')
        cls.EDGE_TTS_VOICE = os.getenv('EDGE_TTS_VOICE', 'en-US-ChristopherNeural')
        cls.CONTENT_TYPE = os.getenv('CONTENT_TYPE', 'drama')
        cls.AUTO_SPLIT = os.getenv('AUTO_SPLIT', 'true').lower() == 'true'

    @classmethod
    def validate(cls) -> bool:
        errors = []
        warnings = []

        if not cls.TIKTOK_SESSION_ID:
            warnings.append('TIKTOK_SESSION_ID manquant — upload désactivé')

        pool = cls.background_pool()
        if not pool:
            errors.append(
                f'Aucune vidéo de fond trouvée dans {cls.ASSETS_DIR} '
                '(ajoutez au moins un .mp4)'
            )

        for w in warnings:
            print(f'[WARNING] {w}')

        if errors:
            for e in errors:
                print(f'[ERROR] {e}')
            return False

        return True


def get_config():
    """Renvoie la config unique du bot. Le niveau de log vient de LOG_LEVEL
    (.env, défaut INFO) — voir Config.LOG_LEVEL."""
    return Config
