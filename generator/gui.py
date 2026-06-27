#!/usr/bin/env python3
"""
TikTok Auto Bot — interface minimale et épurée.
"""

import io
import sys
from pathlib import Path

# Add src/ to path so imports work whether running from root or from src/
sys.path.insert(0, str(Path(__file__).parent))

# En mode PyInstaller --windowed, sys.stdout/stderr valent None. Torch, tqdm
# (Whisper) et edge-tts écrivent dessus pendant l'import et plantent. On fournit
# des flux factices AVANT tout import lourd (config, main → torch/whisper).
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

import logging
import os
import queue
import threading
import tkinter as tk

from dotenv import load_dotenv, set_key

from config import get_config, APP_DIR, BUNDLE_DIR
from main import TikTokAutoBot, MAX_PARALLEL_VIDEOS

# .env vit à côté de l'exe. Au premier lancement, on le crée à partir du modèle
# .env.example embarqué (placeholders + commentaires) pour guider l'utilisateur.
ENV_FILE = APP_DIR / '.env'
if not ENV_FILE.exists():
    template = BUNDLE_DIR / '.env.example'
    if template.exists():
        ENV_FILE.write_text(template.read_text(encoding='utf-8-sig'), encoding='utf-8')
    else:
        ENV_FILE.write_text('', encoding='utf-8')
load_dotenv(ENV_FILE, encoding='utf-8-sig')

LOG_QUEUE: queue.Queue = queue.Queue()

# Palette épurée
BG = '#0e0e12'
CARD = '#17171f'
ACCENT = '#fe2c55'   # rose TikTok
TEXT = '#f2f2f2'
MUTED = '#7a7a85'


class QueueHandler(logging.Handler):
    def emit(self, record):
        LOG_QUEUE.put(self.format(record))


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('TikTok Auto Bot')
        self.geometry('560x830')
        self.minsize(480, 720)
        self.configure(bg=BG)

        self._running = False
        self._stop_event = threading.Event()
        self._build()
        self._load_env()
        self._poll_logs()

    # ── Construction ─────────────────────────────────────────────────

    def _build(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill='both', expand=True, padx=28, pady=24)

        tk.Label(wrap, text='TikTok Auto Bot', bg=BG, fg=TEXT,
                 font=('Segoe UI Semibold', 22)).pack(anchor='w')
        tk.Label(wrap, text='Génération automatique de vidéos', bg=BG, fg=MUTED,
                 font=('Segoe UI', 11)).pack(anchor='w', pady=(0, 22))

        # ── Carte réglages ──
        card = tk.Frame(wrap, bg=CARD)
        card.pack(fill='x')
        pad = dict(padx=18, pady=(14, 0))

        # ── Découpage auto ──
        self._v_auto_split = tk.BooleanVar(value=True)
        split_chk = tk.Checkbutton(
            card, variable=self._v_auto_split,
            text='Découper les longues histoires en parties',
            bg=CARD, fg=MUTED, activebackground=CARD, activeforeground=TEXT,
            selectcolor='#0e0e12', relief='flat', highlightthickness=0,
            font=('Segoe UI', 9), anchor='w', cursor='hand2',
        )
        split_chk.pack(fill='x', padx=16, pady=(12, 0))

        # ── Pool de vidéos de fond ──
        tk.Label(card, text='Vidéos de fond (choisies au hasard)', bg=CARD,
                 fg=MUTED, font=('Segoe UI', 9)).pack(anchor='w', **pad)
        poolline = tk.Frame(card, bg=CARD)
        poolline.pack(fill='x', padx=18, pady=(2, 0))
        self._pool_var = tk.StringVar(value='—')
        tk.Label(poolline, textvariable=self._pool_var, bg=CARD, fg=TEXT,
                 font=('Segoe UI', 10), anchor='w', justify='left',
                 wraplength=380).pack(side='left', fill='x', expand=True)
        tk.Button(poolline, text='Ouvrir', command=self._open_assets,
                  bg='#0e0e12', fg=MUTED, relief='flat', cursor='hand2',
                  activebackground=ACCENT, activeforeground='white',
                  width=7).pack(side='right', padx=(6, 0))

        tk.Frame(card, bg=CARD, height=14).pack()  # spacer bas

        # ── Nombre de vidéos ──
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill='x', pady=(22, 0))
        tk.Label(row, text='Nombre de vidéos', bg=BG, fg=TEXT,
                 font=('Segoe UI', 11)).pack(side='left')
        self._n = tk.IntVar(value=1)
        tk.Spinbox(row, from_=1, to=50, textvariable=self._n, width=5,
                   bg=CARD, fg=TEXT, buttonbackground=CARD, relief='flat',
                   insertbackground=TEXT, justify='center',
                   font=('Segoe UI', 12)).pack(side='right')

        # ── Bouton Générer / Arrêter ──
        self._btn = tk.Button(wrap, text='Générer', command=self._on_run,
                              bg=ACCENT, fg='white', relief='flat',
                              activebackground='#d61f45', activeforeground='white',
                              font=('Segoe UI Semibold', 13), cursor='hand2',
                              pady=11)
        self._btn.pack(fill='x', pady=(22, 8))

        self._status = tk.StringVar(value='Prêt.')
        tk.Label(wrap, textvariable=self._status, bg=BG, fg=MUTED,
                 font=('Segoe UI', 10)).pack(anchor='w')

        # ── Barre de progression + temps restant ──
        self._progress_frame = tk.Frame(wrap, bg=BG)
        prog_top = tk.Frame(self._progress_frame, bg=BG)
        prog_top.pack(fill='x')
        self._progress_pct = tk.StringVar(value='0 %')
        tk.Label(prog_top, textvariable=self._progress_pct, bg=BG, fg=TEXT,
                 font=('Segoe UI Semibold', 10)).pack(side='left')
        self._bar = tk.Canvas(self._progress_frame, height=8, bg=CARD,
                              highlightthickness=0, bd=0)
        self._bar.pack(fill='x', pady=(5, 0))
        self._bar_fill = self._bar.create_rectangle(0, 0, 0, 8, fill=ACCENT, width=0)
        self._bar.bind('<Configure>', lambda e: self._draw_bar(
            min(self._prog_done / self._prog_total, 1.0) if self._prog_total else 0))
        # État de progression (alimenté par le callback)
        self._prog_done = 0
        self._prog_total = 0

        # ── Terminaux parallèles (un par worker actif) ──
        self._lanes_frame = tk.Frame(wrap, bg=BG)
        self._lane_rows, self._lane_vars, self._lane_dots = [], [], []
        for _ in range(MAX_PARALLEL_VIDEOS):
            row = tk.Frame(self._lanes_frame, bg=CARD)
            dot = tk.Label(row, text='●', bg=CARD, fg=MUTED, font=('Segoe UI', 11))
            dot.pack(side='left', padx=(10, 8), pady=4)
            var = tk.StringVar(value='—')
            tk.Label(row, textvariable=var, bg=CARD, fg=TEXT, anchor='w',
                     font=('Consolas', 9)).pack(side='left', fill='x', expand=True)
            self._lane_rows.append(row)
            self._lane_vars.append(var)
            self._lane_dots.append(dot)

        # ── Console logs ──
        self._log = tk.Text(wrap, height=8, bg='#08080b', fg='#b8b8c0',
                            relief='flat', font=('Consolas', 9), wrap='word',
                            state='disabled', padx=10, pady=8)
        self._log.pack(fill='both', expand=True, pady=(12, 0))

    # ── Barre de progression ──────────────────────────────────────────

    def _progress_callback(self, done, total):
        """Appelé depuis le thread worker — on repasse sur le thread Tk."""
        self.after(0, self._update_progress, done, total)

    def _update_progress(self, done, total):
        self._prog_done = done
        self._prog_total = max(total, 1)
        frac = min(done / self._prog_total, 1.0)
        self._draw_bar(frac)
        self._progress_pct.set(f'{int(frac * 100)} %  ·  {done}/{self._prog_total} vidéo(s)')

    def _draw_bar(self, frac):
        w = self._bar.winfo_width() or 1
        self._bar.coords(self._bar_fill, 0, 0, int(w * frac), 8)

    def _show_progress(self, show):
        if show:
            self._progress_frame.pack(fill='x', pady=(10, 0))
        else:
            self._progress_frame.pack_forget()

    # ── Terminaux parallèles ──────────────────────────────────────────

    def _show_lanes(self, count):
        """Affiche `count` terminaux (un par worker), tous au repos."""
        self._lanes_frame.pack(fill='x', pady=(8, 0))
        for i, row in enumerate(self._lane_rows):
            if i < count:
                row.pack(fill='x', pady=2)
                self._lane_vars[i].set('—')
                self._lane_dots[i].configure(fg=MUTED)
            else:
                row.pack_forget()

    def _hide_lanes(self):
        self._lanes_frame.pack_forget()

    def _activity_callback(self, slot, text):
        """Appelé depuis un thread worker — on repasse sur le thread Tk."""
        self.after(0, self._set_lane, slot, text)

    def _set_lane(self, slot, text):
        if 0 <= slot < len(self._lane_vars):
            self._lane_vars[slot].set(text or '—')
            self._lane_dots[slot].configure(fg=ACCENT if text else MUTED)

    # ── Pool de vidéos ───────────────────────────────────────────────

    def _open_assets(self):
        cfg = get_config()
        cfg.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(cfg.ASSETS_DIR)

    def _refresh_pool(self):
        cfg = get_config()
        pool = cfg.background_pool()
        if pool:
            self._pool_var.set('  •  '.join(p.name for p in pool))
        else:
            self._pool_var.set('Aucune vidéo — cliquez « Ouvrir » et ajoutez des .mp4')

    # ── Config .env ──────────────────────────────────────────────────

    def _load_env(self):
        self._v_auto_split.set(os.getenv('AUTO_SPLIT', 'true').lower() == 'true')
        self._refresh_pool()

    def _save_env(self):
        set_key(str(ENV_FILE), 'AUTO_SPLIT',
                'true' if self._v_auto_split.get() else 'false')

    # ── Logs ─────────────────────────────────────────────────────────

    def _poll_logs(self):
        try:
            while True:
                msg = LOG_QUEUE.get_nowait()
                self._log.configure(state='normal')
                self._log.insert('end', msg + '\n')
                self._log.see('end')
                self._log.configure(state='disabled')
        except queue.Empty:
            pass
        self.after(150, self._poll_logs)

    # ── Lancement / Arrêt ────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            return
        self._save_env()
        self._running = True
        self._stop_event.clear()
        self._btn.configure(text='Arrêter', bg='#333340',
                            activebackground='#444455', command=self._on_stop)
        self._status.set(f'Génération de {self._n.get()} histoire(s)…')

        # Réinitialise la barre de progression.
        self._prog_done = 0
        self._prog_total = 0
        self._draw_bar(0)
        self._progress_pct.set('0 %')
        self._show_progress(True)
        # Affiche un terminal par worker parallèle (borné par le nb d'histoires).
        self._show_lanes(max(min(MAX_PARALLEL_VIDEOS, self._n.get()), 1))

        # content_type=None → run() suit CONTENT_TYPE du .env (défaut : drama).
        threading.Thread(target=self._run,
                         args=(self._n.get(), None, self._stop_event),
                         daemon=True).start()

    def _on_stop(self):
        self._stop_event.set()
        self._btn.configure(state='disabled', text='Arrêt en cours…')
        self._status.set('Arrêt demandé — fin de la partie en cours…')

    def _run(self, n, content_type, stop_event):
        try:
            load_dotenv(ENV_FILE, override=True, encoding='utf-8-sig')
            get_config().reload()  # relit .env (credentials Reddit, rate, etc.)
            TikTokAutoBot().run(n, content_type=content_type, stop_event=stop_event,
                                progress_callback=self._progress_callback,
                                activity_callback=self._activity_callback)
            if stop_event.is_set():
                self.after(0, self._done, 'Arrêté.')
            else:
                self.after(0, self._done, 'Terminé.')
        except SystemExit:
            self.after(0, self._done, 'Erreur de configuration.')
        except Exception as e:
            self.after(0, self._done, f'Erreur : {e}')

    def _done(self, msg):
        self._running = False
        self._btn.configure(state='normal', text='Générer',
                            bg=ACCENT, activebackground='#d61f45',
                            command=self._on_run)
        self._status.set(msg)
        self._hide_lanes()
        # Barre pleine si terminé normalement, sinon on la fige où elle en est.
        if msg == 'Terminé.' and self._prog_total:
            self._draw_bar(1.0)
            self._progress_pct.set(f'100 %  ·  {self._prog_total}/{self._prog_total} vidéo(s)')


def _setup_logging():
    h = QueueHandler()
    h.setFormatter(logging.Formatter('%(asctime)s  %(message)s', datefmt='%H:%M:%S'))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)


if __name__ == '__main__':
    import sys
    if '--run' in sys.argv:
        n = 1
        idx = sys.argv.index('--run')
        if idx + 1 < len(sys.argv):
            n = int(sys.argv[idx + 1])
        ctype = None
        if '--type' in sys.argv:
            ti = sys.argv.index('--type')
            if ti + 1 < len(sys.argv):
                ctype = sys.argv[ti + 1]
        TikTokAutoBot().run(n, content_type=ctype)
    else:
        _setup_logging()
        App().mainloop()
