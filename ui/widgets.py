"""Widgets réutilisables : badge d'état, carte, ligne d'info."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLabel, QHBoxLayout, QVBoxLayout, QWidget, QSizePolicy,
)

from . import theme


class StatusBadge(QLabel):
    """Pastille + libellé d'état coloré (configured/standby/error…)."""

    def __init__(self, state: str = "standby", text: str | None = None):
        super().__init__()
        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None):
        color = theme.STATE_COLORS.get(state, theme.TEXT_DIM)
        label = text or state.capitalize()
        self.setText(f"●  {label}")
        self.setStyleSheet(
            f"color:{color}; font-weight:600; font-size:12px; "
            f"background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
            f"border-radius:10px; padding:3px 10px;")


class Card(QFrame):
    """Conteneur 'carte' avec coins arrondis et marge interne."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)


def title(text: str, object_name: str = "H1") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(object_name)
    return lbl


def dim(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Dim")
    lbl.setWordWrap(True)
    return lbl


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


def metric(value: str, caption: str) -> Card:
    card = Card()
    v = QLabel(value)
    v.setObjectName("Metric")
    c = QLabel(caption)
    c.setObjectName("Dim")
    card.body.addWidget(v)
    card.body.addWidget(c)
    card._value_label = v  # accès pour mise à jour
    return card


def row(*widgets, spacing: int = 8) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if isinstance(x, QWidget):
            lay.addWidget(x)
        else:
            lay.addLayout(x)
    return w
