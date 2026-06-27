"""Thème sombre « doux pour les yeux » : palette + feuille de style Qt (QSS).

Couleurs froides, faible contraste agressif, accents discrets. Pensé pour un
usage prolongé et un rendu « logiciel commercial ».
"""

from __future__ import annotations

# ── Palette ─────────────────────────────────────────────────────────────
BG = "#0f1115"          # fond application
SURFACE = "#171a21"     # panneaux / cartes
SURFACE_2 = "#1e222b"   # cartes survolées / champs
BORDER = "#272c37"
TEXT = "#e6e8ec"
TEXT_DIM = "#9aa3b2"
TEXT_FAINT = "#6b7280"

ACCENT = "#6ea8fe"      # bleu doux
ACCENT_HOVER = "#84b6ff"
ACCENT_PRESSED = "#5b95e8"

OK = "#5dd39e"          # connecté / succès
WARN = "#e7b35a"        # standby / cooldown
ERR = "#e06c75"         # erreur / échec
INFO = "#7aa2f7"

STATE_COLORS = {
    "configured": OK,
    "standby": WARN,
    "error": ERR,
    "success": OK,
    "failed": ERR,
    "running": INFO,
    "pending": TEXT_DIM,
    "skipped": TEXT_FAINT,
}


def qss() -> str:
    return f"""
* {{
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QWidget#Root {{ background: {BG}; }}

/* Sidebar */
QWidget#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#Brand {{ font-size: 18px; font-weight: 700; padding: 18px 18px 4px 18px; }}
QLabel#BrandSub {{ color: {TEXT_FAINT}; font-size: 11px; padding: 0 18px 14px 18px; }}

QPushButton#NavBtn {{
    background: transparent; border: none; text-align: left;
    padding: 11px 18px; border-radius: 8px; color: {TEXT_DIM};
    font-size: 14px; margin: 2px 10px;
}}
QPushButton#NavBtn:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#NavBtn:checked {{ background: {SURFACE_2}; color: {ACCENT};
    border-left: 3px solid {ACCENT}; }}

/* Cartes */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Card:hover {{ border: 1px solid {ACCENT}; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#CardNote {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#H1 {{ font-size: 22px; font-weight: 700; }}
QLabel#H2 {{ font-size: 16px; font-weight: 600; }}
QLabel#H3 {{ font-size: 14px; font-weight: 600; color: {TEXT_DIM}; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Metric {{ font-size: 28px; font-weight: 700; color: {ACCENT}; }}

/* Boutons */
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 16px; color: {TEXT};
}}
QPushButton:hover {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #0b0d12; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Danger {{ border: 1px solid {ERR}; color: {ERR}; }}
QPushButton#Danger:hover {{ background: {ERR}; color: #0b0d12; }}

/* Champs */
QLineEdit, QSpinBox, QComboBox, QTimeEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTimeEdit:focus {{
    border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; selection-color: #0b0d12; }}

/* Tableaux & listes */
QTableWidget, QListWidget, QTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background: {SURFACE_2}; color: {TEXT_DIM}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600;
}}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {BORDER}; }}
QTableWidget::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}

/* Divers */
QProgressBar {{ background: {SURFACE_2}; border: none; border-radius: 6px;
    height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px; }}
"""
