"""Keyboard-first command palette (Ctrl+K).

Every sidebar action is registered by name, so a power user never has to hunt
through menus.  Scoring is a cheap fuzzy subsequence match that favours prefix
and word-start hits.
"""
import traceback

import tkinter as tk

from .theme import (
    SURFACE, BORDER, TEXT, TEXT_MUTED, ACCENT, ACCENT_SOFT, FONT, FONT_SMALL,
)

MAX_ROWS = 9
TITLE_WIDTH = 44


def score(query, title):
    """Fuzzy score of ``query`` against ``title``; 0 means "no match".

    Exact prefix beats word-start matches, which beat loose subsequences.
    """
    q = query.lower().strip()
    t = title.lower()
    if not q:
        return 1
    if t.startswith(q):
        return 100 - len(t) * 0.1
    words = t.split()
    if any(w.startswith(q) for w in words):
        return 80 - len(t) * 0.1
    score_value = 0
    pos = 0
    at_word_start = False
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return 0
        if found == pos and pos > 0:
            score_value += 4 if at_word_start else 2
        else:
            score_value += 1
        at_word_start = found == 0 or t[found - 1] in " -_/("
        pos = found + 1
    return score_value


class CommandPalette(tk.Toplevel):
    """Searchable list of every action the app exposes."""

    def __init__(self, app, commands):
        super().__init__(app.root, bg=SURFACE)
        self.app = app
        self.commands = list(commands)
        self._filtered = []
        self._open = False

        self.withdraw()
        self.overrideredirect(True)
        self.transient(app.root)
        self.attributes("-topmost", True)
        try:
            self.attributes("-type", "popup_menu")
        except tk.TclError:
            pass

        outer = tk.Frame(self, bg=SURFACE, highlightthickness=1,
                         highlightbackground=ACCENT)
        outer.pack(fill=tk.BOTH, expand=True)
        self.entry = tk.Entry(outer, font=FONT, relief=tk.FLAT, bg=SURFACE, fg=TEXT,
                              insertbackground=TEXT, highlightthickness=0)
        self.entry.pack(fill=tk.X, padx=12, pady=(10, 6))
        tk.Frame(outer, bg=BORDER, height=1).pack(fill=tk.X)

        self.listbox = tk.Listbox(outer, height=MAX_ROWS, font=FONT_SMALL, bg=SURFACE,
                                 fg=TEXT, relief=tk.FLAT, highlightthickness=0,
                                 activestyle="none", selectbackground=ACCENT_SOFT,
                                 selectforeground=TEXT, exportselection=False)
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.footer = tk.Label(outer, text="", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED,
                               anchor="w", padx=12, pady=6)
        self.footer.pack(fill=tk.X)

        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Return>", lambda e: self._invoke())
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._move(-1))
        self.entry.bind("<Escape>", lambda e: self.close())
        self.listbox.bind("<Button-1>", self._on_click)
        self.listbox.bind("<Double-Button-1>", lambda e: self._invoke())
        self.bind("<FocusOut>", self._on_focus_out)
        self._filter()

    # ---------- lifecycle ----------

    def open(self):
        if self._open:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.entry.focus_set()
            return
        self._open = True
        self.entry.delete(0, tk.END)
        self._filter()
        self._place()
        self.deiconify()
        self.lift()
        self.focus_force()
        self.entry.focus_set()

    def close(self):
        self._open = False
        self.withdraw()

    def _place(self):
        root = self.app.root
        root.update_idletasks()
        width = 560
        rows = max(1, min(MAX_ROWS, len(self._filtered)))
        height = 46 + rows * 22 + 34
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + 96
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _on_focus_out(self, _event=None):
        self.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self):
        try:
            if self.focus_get() is None or not str(self.focus_get()).startswith(str(self)):
                self.close()
        except (tk.TclError, KeyError):
            self.close()

    # ---------- filtering ----------

    def _filter(self):
        query = self.entry.get()
        scored = []
        for index, (title, _fn, shortcut) in enumerate(self.commands):
            value = score(query, title)
            if value:
                scored.append((value, index, title, shortcut))
        if query:
            scored.sort(key=lambda item: (-item[0], item[2]))
        else:
            scored.sort(key=lambda item: item[1])
        shown = scored[:MAX_ROWS]
        self.listbox.delete(0, tk.END)
        self._filtered = []
        for _value, index, title, shortcut in shown:
            self._filtered.append(index)
            pad = max(1, TITLE_WIDTH - len(title))
            self.listbox.insert(tk.END, title + "\u2003" * 0 + " " * pad + (shortcut or ""))
        if self._filtered:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)
            self.listbox.activate(0)
        self.footer.config(text=f"{len(scored)} command(s)"
                               + ("  \u00b7  Enter to run" if self._filtered else ""))
        self._resize()

    def _resize(self):
        if not self._open:
            return
        rows = max(1, min(MAX_ROWS, len(self._filtered)))
        height = 46 + rows * 22 + 34
        try:
            self.geometry(f"560x{height}+{self.winfo_x()}+{self.winfo_y()}")
        except tk.TclError:
            pass

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        self._filter()

    # ---------- selection ----------

    def _move(self, delta):
        if not self._filtered:
            return "break"
        current = self.listbox.curselection()
        row = (current[0] if current else 0) + delta
        row %= len(self._filtered)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(row)
        self.listbox.activate(row)
        self.listbox.see(row)
        return "break"

    def _on_click(self, event):
        row = self.listbox.nearest(event.y)
        if row < 0 or row >= len(self._filtered):
            return
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(row)
        self.listbox.activate(row)

    def _invoke(self):
        current = self.listbox.curselection()
        if not current or not self._filtered:
            return "break"
        index = self._filtered[current[0]]
        title, fn, _shortcut = self.commands[index]
        self.close()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a broken action must not kill the app
            traceback.print_exc()
        self.app.status(f"Ran: {title}")
        return "break"