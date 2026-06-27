"""Installe la routine matinale au démarrage Windows.

Crée un raccourci dans le dossier Startup de l'utilisateur qui lance
morning_routine.py en tâche de fond (pythonw = pas de fenêtre console).

Usage :
    python install_startup.py          # installe
    python install_startup.py --remove # désinstalle
"""

from __future__ import annotations

import sys
import argparse
import winreg
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "morning_routine.py"
PYTHONW = Path(sys.executable).parent / "pythonw.exe"
STARTUP = (
    Path.home()
    / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
)
SHORTCUT_NAME = "ContentHub Morning Routine.lnk"


def install():
    try:
        import pythoncom
        from win32com.client import Dispatch
    except ImportError:
        print("[ERREUR] pywin32 requis : pip install pywin32")
        sys.exit(1)

    pythoncom.CoInitialize()
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(STARTUP / SHORTCUT_NAME))
    shortcut.TargetPath = str(PYTHONW if PYTHONW.exists() else sys.executable)
    shortcut.Arguments = f'"{SCRIPT}"'
    shortcut.WorkingDirectory = str(SCRIPT.parent)
    shortcut.Description = "ContentHub — routine matinale automatique"
    shortcut.IconLocation = str(SCRIPT.parent / "dist" / "ContentHub" / "ContentHub.exe")
    shortcut.save()
    print(f"[OK] Raccourci créé : {STARTUP / SHORTCUT_NAME}")
    print("     La routine démarrera automatiquement à la prochaine session Windows.")
    print(f"     Pour configurer l'heure : python morning_routine.py --config")


def remove():
    target = STARTUP / SHORTCUT_NAME
    if target.exists():
        target.unlink()
        print(f"[OK] Raccourci supprimé : {target}")
    else:
        print("[INFO] Aucun raccourci à supprimer.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true", help="Désinstalle")
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        install()


if __name__ == "__main__":
    main()
