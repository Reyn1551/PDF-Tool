"""Custom widgets for PDF Tool: hover buttons, sidebar items, window helpers."""
import os

import tkinter as tk

from .theme import (
    BG, SURFACE, SIDEBAR, BORDER, TEXT, TEXT_MUTED, ACCENT, ACCENT_HOV,
    ACCENT_SOFT, HOVER, DANGER, FONT, FONT_SMALL, FONT_BOLD, FONT_TITLE,
)


def _safe_grab(win):
    try:
        win.grab_set()
    except tk.TclError:
        win.after(50, lambda: _safe_grab(win))
    return win


def _set_app_icon(root):
    """Use the PNG icon from assets/ if available; silently skip if not found."""
    try:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
        if os.path.isfile(icon_path):
            root.iconphoto(True, tk.PhotoImage(file=icon_path))
    except Exception:
        pass


class HoverButton(tk.Frame):
    """Flat button with hover state."""

    def __init__(self, master, text, command=None, primary=False, danger=False, small=False,
                 padx=14, pady=7, tooltip=None):
        bg = ACCENT if primary else (DANGER if danger else SURFACE)
        fg = "#ffffff" if (primary or danger) else TEXT
        hover_bg = ACCENT_HOV if primary else (DANGER if danger else HOVER)
        font = FONT_SMALL if small else FONT
        super().__init__(master, bg=bg, cursor="hand2", highlightthickness=1,
                         highlightbackground=bg, highlightcolor=bg)
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font, cursor="hand2",
                              padx=padx, pady=pady)
        self.label.pack()
        self._state = "normal"
        self._hover_bg = hover_bg
        self._orig_bg = bg
        self._tooltip = tooltip
        self._tip_win = None

        def on_enter(e):
            if self._state == "normal":
                self._set_bg(hover_bg)
                if self._tooltip:
                    self._show_tip(e)

        def on_leave(e):
            self._set_bg(self._orig_bg)
            self._hide_tip()

        def on_click(e):
            if self._state == "normal" and command:
                command()

        self.bind("<Enter>", on_enter)
        self.bind("<Leave>", on_leave)
        self.bind("<Button-1>", on_click)
        self.label.bind("<Enter>", on_enter)
        self.label.bind("<Leave>", on_leave)
        self.label.bind("<Button-1>", on_click)

    def _set_bg(self, bg):
        self.config(bg=bg, highlightbackground=bg, highlightcolor=bg)
        self.label.config(bg=bg)

    def _show_tip(self, e):
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        self._tip_win = tk.Toplevel(self)
        self._tip_win.wm_overrideredirect(True)
        self._tip_win.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip_win, text=self._tooltip, bg="#1f2328", fg="#ffffff",
                 font=FONT_SMALL, padx=8, pady=4).pack()

    def _hide_tip(self):
        if self._tip_win:
            try:
                if self._tip_win.winfo_exists():
                    self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None

    def set_enabled(self, enabled):
        self._state = "normal" if enabled else "disabled"
        bg = "#e9ebef" if not enabled else self._orig_bg
        fg = "#9ca3af" if not enabled else (TEXT if not (self._orig_bg == ACCENT or self._orig_bg == DANGER) else "#ffffff")
        self._set_bg(bg)
        self.label.config(fg=fg)


class SidebarItem(tk.Frame):
    """Sidebar menu item with hover."""

    def __init__(self, master, text, command=None, accent=False):
        super().__init__(master, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.text = text
        self.accent = accent
        self.label = tk.Label(self, text=text, bg=SIDEBAR, fg=TEXT,
                              font=FONT, anchor="w", padx=12, pady=8, cursor="hand2")
        self.label.pack(fill=tk.X)
        self._enabled = True
        self.bind("<Enter>", lambda e: self._hover())
        self.bind("<Leave>", lambda e: self._unhover())
        self.bind("<Button-1>", lambda e: self._click())
        self.label.bind("<Enter>", lambda e: self._hover())
        self.label.bind("<Leave>", lambda e: self._unhover())
        self.label.bind("<Button-1>", lambda e: self._click())

    def _hover(self):
        if self._enabled:
            self.config(bg=HOVER)
            self.label.config(bg=HOVER)

    def _unhover(self):
        self.config(bg=SIDEBAR)
        self.label.config(bg=SIDEBAR)

    def _click(self):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        fg = TEXT if enabled else "#9ca3af"
        self.label.config(fg=fg)
