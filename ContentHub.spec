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

# Logos officiels des réseaux (SVG) embarqués pour l'UI (cf. ui/brand.py, qui
# les recherche sous _MEIPASS/ui/assets/logos en mode figé).
_logos = os.path.join(os.getcwd(), 'ui', 'assets', 'logos')
if os.path.exists(_logos):
    datas.append((_logos, os.path.join('ui', 'assets', 'logos')))

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
