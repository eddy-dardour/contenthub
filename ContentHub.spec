# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour ContentHub.

L'exécutable n'embarque QUE la plateforme (PySide6 + requests + cryptography).
L'outil de génération `generator/` est embarqué comme DONNÉES et lancé en
sous-processus via le Python système (cf. core/generator.py) : ses lourdes
dépendances (torch/whisper) ne gonflent donc pas l'exe.

Ressources embarquées (lecture seule, retrouvées via core.paths.bundle_dir) :
  generator/  resources/{assets,ffmpeg,models}

Build :
    cd contenthub
    pyinstaller ContentHub.spec --noconfirm
Sortie : contenthub/dist/ContentHub/ContentHub.exe
"""

import os

ROOT = os.getcwd()  # contenthub/ (racine autonome)

def res(rel):
    return os.path.join(ROOT, rel)

datas = []
for src, dest in [
    ('generator', 'generator'),
    ('resources', 'resources'),
]:
    p = res(src)
    if os.path.exists(p):
        datas.append((p, dest))

# Assets UI : logos réseaux (SVG) + icône app (SVG/PNG/ICO)
_assets = os.path.join(os.getcwd(), 'ui', 'assets')
if os.path.exists(_assets):
    datas.append((_assets, os.path.join('ui', 'assets')))

# Scripts de démarrage (api_server.py, start_ngrok_tunnel.py, setup_ngrok.py)
for script in ['api_server.py', 'start_ngrok_tunnel.py', 'setup_ngrok.py', 'morning_routine.py']:
    script_path = res(script)
    if os.path.exists(script_path):
        datas.append((script_path, '.'))

hiddenimports = [
    'networks.tiktok', 'networks.youtube', 'networks.facebook',
    # Rendu des logos SVG des réseaux (ui/brand.py).
    'PySide6.QtSvg',
]

a = Analysis(
    ['app.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclut les dépendances lourdes de manual/ : elles ne servent qu'au
    # sous-processus (Python système), pas à l'exe de la plateforme.
    excludes=[
        'torch', 'whisper', 'tiktoken', 'cv2', 'imageio', 'matplotlib',
        'pandas', 'numpy.f2py', 'PyQt5', 'PyQt6', 'PySide2', 'tkinter',
        'IPython', 'pytest', 'notebook',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

_icon = os.path.join(os.getcwd(), 'ui', 'assets', 'icon.ico')
if not os.path.exists(_icon):
    _icon = os.path.join(os.getcwd(), 'ui', 'assets', 'icon.png')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ContentHub',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_icon if os.path.exists(_icon) else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ContentHub',
)
