#!/usr/bin/env python3
"""
Construit l'exécutable Windows (dossier dist/TikTokAutoBot/).

Mode --onedir (à cause de PyTorch/Whisper trop lourds pour --onefile).
Embarque ffmpeg/ffprobe, les vidéos de fond, la police et Whisper.
Le modèle Whisper se télécharge au 1er lancement (vers models/).

Usage :
    python build_exe.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENTRY = ROOT / 'src' / 'gui.py'
SEP = ';'


def main():
    add_data = []

    # Source files (gui.py, main.py, config.py, etc. from src/)
    src_dir = ROOT / 'src'
    if src_dir.exists():
        add_data.append(f'{src_dir}{SEP}.')

    # ffmpeg + ffprobe embarqués
    ff_dir = ROOT / 'ffmpeg'
    if (ff_dir / 'ffmpeg.exe').exists():
        add_data.append(f'{ff_dir}{SEP}ffmpeg')
    else:
        print('[ATTENTION] ffmpeg/ non trouvé — le .exe ne sera pas autonome.')

    # vidéos de fond embarquées (fallback si pas de assets/ à côté de l'exe)
    assets = ROOT / 'assets'
    if assets.exists() and any(assets.iterdir()):
        add_data.append(f'{assets}{SEP}assets')

    # police des sous-titres (style TikTok viral — Komika Axis, rond/cartoon)
    font = ROOT / 'assets' / 'fonts' / 'KomikaAxis.ttf'
    if not font.exists():
        print('[ATTENTION] police KomikaAxis.ttf absente dans assets/fonts/.')

    # modèle .env (placeholders) : copié à côté de l'exe au 1er lancement
    env_example = ROOT / '.env.example'
    if env_example.exists():
        add_data.append(f'{env_example}{SEP}.')

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--onedir', '--windowed',
        '--name', 'TikTokAutoBot',
        # TTS / Reddit
        '--hidden-import', 'edge_tts',
        '--collect-all', 'edge_tts',
        # Whisper + dépendances (sous-titres hors-ligne)
        '--hidden-import', 'whisper',
        '--collect-all', 'whisper',
        '--hidden-import', 'tiktoken',
        '--collect-all', 'tiktoken',
        '--collect-all', 'torch',
        # Pillow : carte Reddit en début de vidéo (import paresseux → explicite).
        '--hidden-import', 'PIL',
        '--collect-all', 'PIL',
        # BeautifulSoup : scraping Quora (source de contenu hors Reddit).
        '--hidden-import', 'bs4',
        '--collect-all', 'bs4',
        # pkg_resources importe appdirs dynamiquement (via .extern) au runtime :
        # PyInstaller ne le voit pas → il faut l'embarquer explicitement, sinon
        # crash au démarrage ("The 'appdirs' package is required").
        '--hidden-import', 'appdirs',
        '--collect-all', 'pkg_resources',
    ]

    # Modules tirés transitivement mais JAMAIS utilisés par le pipeline
    # (scrape → tts → whisper → ffmpeg). Les exclure allège fortement le bundle :
    #   cv2 ~97 Mo, imageio_ffmpeg ~62 Mo (2e ffmpeg redondant), pyarrow ~77 Mo,
    #   + outils de dev.
    # NB : on n'exclut PAS scipy/llvmlite/numba (utilisés par Whisper word_timestamps).
    exclude = [
        'cv2', 'imageio', 'imageio_ffmpeg',     # vision/IO inutilisés (~160 Mo)
        'matplotlib', 'pandas', 'pyarrow',      # data-science inutile ici (~77 Mo)
        'jedi', 'IPython', 'pythonwin',         # outils de dev/autocomplétion
        'pytest', 'notebook', 'PyQt5', 'PySide2',
    ]
    for m in exclude:
        cmd += ['--exclude-module', m]
    for d in add_data:
        cmd += ['--add-data', d]
    cmd.append(str(ENTRY))

    print('>', ' '.join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    exe = ROOT / 'dist' / 'TikTokAutoBot' / 'TikTokAutoBot.exe'
    print()
    if exe.exists():
        print(f'[OK] Application prete : {exe}')
        print('     Lancez TikTokAutoBot.exe dans ce dossier.')
        print('     Le modele Whisper se telecharge au 1er lancement (~74 Mo).')
    else:
        print('[ERREUR] .exe non trouve.')


if __name__ == '__main__':
    main()
