"""Application shell: layout, menus, shortcuts, page grid, status bar.

Action logic lives in the ``actions_*`` modules (mixins).  This module owns the
*host* contracts those mixins rely on:

* ``tool`` - the :class:`pdf_ops.PDFTool` document model (undo, search, ...)
* ``status(text, tone)`` - one-line feedback in the status bar
* ``_refresh_preview()`` / ``_refresh_status()`` / ``_sync_enabled()``
* ``_apply_pdf_bytes(data, label)`` - swap in a worker result as one undo step
* ``_show_hit(hit)`` - scroll to + flash a search hit
* ``jobs`` / ``thumbs`` / ``recent`` - background work, thumbnails, history

Performance rules that keep the UI responsive:
  * page rasterisation is lazy and virtualised (only visible cards request
    bitmaps, and creation is chunked so a 1000-page file never freezes Tk);
  * every slow operation runs through :class:`pdftool.jobs.JobRunner`;
  * mutations snapshot for undo automatically (see ``PDFTool._auto_checkpoint``).
"""
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from .theme import (
    BG, SURFACE, SIDEBAR, BORDER, TEXT, TEXT_MUTED, ACCENT, ACCENT_SOFT,
    DANGER, FONT_SMALL, FONT_BOLD, FONT_TITLE,
)
from .widgets import HoverButton, SidebarItem, _set_app_icon
from .jobs import JobRunner, run_job
from .thumbs import ThumbLoader
from .search import SearchPanel
from .palette import CommandPalette
from .recent import RecentStore
from .dnd import enable_drop
from .actions_organize import OrganizeActions
from .actions_security import SecurityActions
from .actions_export import ExportActions
from .actions_edit import EditActions
from .actions_convert import ConvertActions
from .actions_pages_extra import PagesExtraActions
from .actions_redact import RedactActions
from .actions_convert_extra import ConvertExtraActions
from pdf_ops import PDFTool

CARD_PAD = 12
ZOOM_STEPS = (90, 120, 150, 190, 240, 300)


class PDFToolApp(
    OrganizeActions,
    SecurityActions,
    ExportActions,
    EditActions,
    ConvertActions,
    PagesExtraActions,
    RedactActions,
    ConvertExtraActions,
):
    def __init__(self, root):
        self.root = root
        self.tool = PDFTool()
        self.jobs = JobRunner(root)
        self.thumbs = ThumbLoader(root)
        self.recent = RecentStore()
        self.zoom_index = 2
        self._cards = {}          # page index -> card widget record
        self._measured_row_h = 0  # row height learned from a real card
        self._spare_cards = []    # detached cards available for reuse
        self._region_key = None   # (rows, row_h, width) already laid out
        self._grid_cols = 4
        self._thumb_requests = set()
        self._visible_job = None
        self._search_panel = None
        self._palette = None
        self._closing = False

        root.title("PDF Tool")
        root.geometry("1120x740")
        root.minsize(920, 580)
        root.configure(bg=BG)
        _set_app_icon(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_header()
        self._build_body()
        self._build_status()
        self._build_menu()
        self._bind_shortcuts()
        self._sync_enabled()
        self._refresh_status()
        self._enable_drop()

    # ------------------------------------------------------------ drag & drop
    def _handle_drop(self, paths):
        """Act on files dropped onto the window (first PDF wins)."""
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        if not pdfs:
            self.status("Only PDF files can be dropped", "error")
            return
        self.open_path(pdfs[0])
        if len(pdfs) > 1:
            self.status(f"Opened {os.path.basename(pdfs[0])} "
                        f"({len(pdfs) - 1} more file(s) ignored)", "accent")

    def _enable_drop(self):
        """Accept files dropped on the window when ``tkinterdnd2`` is present."""
        extra = [w for w in (getattr(self, "canvas", None),
                             getattr(self, "frame", None),
                             getattr(self, "sidebar_canvas", None)) if w is not None]
        self.root = enable_drop(self.root, self._handle_drop, widgets=extra)

    # ------------------------------------------------------------------ header
    def _build_header(self):
        head = tk.Frame(self.root, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER)
        head.pack(side=tk.TOP, fill=tk.X)

        dot = tk.Canvas(head, width=16, height=16, bg=SURFACE, highlightthickness=0)
        dot.create_oval(2, 2, 14, 14, fill=ACCENT, outline="")
        dot.pack(side=tk.LEFT, padx=(16, 8), pady=12)
        tk.Label(head, text="PDF Tool", font=FONT_TITLE, bg=SURFACE,
                 fg=TEXT).pack(side=tk.LEFT)

        self.file_label = tk.Label(head, text="No file open", font=FONT_SMALL,
                                   bg=SURFACE, fg=TEXT_MUTED)
        self.file_label.pack(side=tk.LEFT, padx=16)

        self.btn_save = HoverButton(head, "Save As", command=self.save_as,
                                    primary=True, small=True)
        self.btn_save.pack(side=tk.RIGHT, padx=(4, 16), pady=8)
        self.btn_open = HoverButton(head, "Open", command=self.open_pdf, small=True)
        self.btn_open.pack(side=tk.RIGHT, padx=4, pady=8)

        self.btn_redo = HoverButton(head, "\u21b7", command=self.redo, small=True,
                                    padx=9, tooltip="Redo (Ctrl+Shift+Z)")
        self.btn_redo.pack(side=tk.RIGHT, padx=2, pady=8)
        self.btn_undo = HoverButton(head, "\u21b6", command=self.undo, small=True,
                                    padx=9, tooltip="Undo (Ctrl+Z)")
        self.btn_undo.pack(side=tk.RIGHT, padx=2, pady=8)

    # -------------------------------------------------------------------- body
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(body)
        self._build_preview(body)
        self.root.bind_all("<MouseWheel>", self._route_wheel)
        self.root.bind_all("<Button-4>", self._route_wheel)
        self.root.bind_all("<Button-5>", self._route_wheel)

    def _route_wheel(self, e):
        up = getattr(e, "num", None) == 4 or getattr(e, "delta", 0) > 0
        step = -2 if up else 2
        target = self.canvas
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
            while w is not None:
                if w is getattr(self, "sidebar_canvas", None):
                    target = self.sidebar_canvas
                    break
                w = getattr(w, "master", None)
        except Exception:
            pass
        try:
            target.yview_scroll(step, "units")
        except tk.TclError:
            pass
        if target is self.canvas:
            self._schedule_visible_update()

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self, parent):
        outer = tk.Frame(parent, bg=SIDEBAR, width=236, highlightthickness=1,
                         highlightbackground=BORDER)
        outer.pack(side=tk.LEFT, fill=tk.Y)
        outer.pack_propagate(False)

        self.sidebar_canvas = tk.Canvas(outer, bg=SIDEBAR, highlightthickness=0,
                                        width=236)
        sbs = tk.Scrollbar(outer, orient=tk.VERTICAL, command=self.sidebar_canvas.yview)
        self.sidebar_canvas.configure(yscrollcommand=sbs.set)
        sbs.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(self.sidebar_canvas, bg=SIDEBAR)
        self.sidebar_canvas.create_window((0, 0), window=inner, anchor="nw",
                                          tags="inner")
        inner.bind("<Configure>", lambda e: self.sidebar_canvas.configure(
            scrollregion=self.sidebar_canvas.bbox("all")))
        self.sidebar_canvas.bind("<Configure>", lambda e: self.sidebar_canvas.itemconfig(
            "inner", width=e.width))

        self.sidebar_items = []
        self.sidebar_groups = []
        self._menu_groups = []
        for title, entries, standalone in self._collect_sidebar_entries():
            self.sidebar_groups.append((title, list(entries)))
            self._sidebar_group(inner, title, entries, standalone)
            self._menu_groups.append((title, entries))

    def _collect_sidebar_entries(self):
        """Merge registries from every mixin, in MRO order, without duplicates.

        Mixins expose ``sidebar_entries()`` (groups) and may expose
        ``standalone_methods()`` (actions that work without an open document).
        """
        groups, seen, standalone = [], set(), set()
        for cls in type(self).__mro__:
            registry = cls.__dict__.get("sidebar_entries")
            if registry is not None:
                for title, entries in registry(self) or ():
                    fresh = []
                    for label, method in entries:
                        key = (title, label)
                        if key in seen:
                            continue
                        action = (getattr(self, method, None)
                                  if isinstance(method, str) else method)
                        if action is None:
                            continue      # stale registry entry: never break the UI
                        seen.add(key)
                        fresh.append((label, action))
                    if fresh:
                        groups.append((title, fresh))
            extra = cls.__dict__.get("standalone_methods")
            if extra is not None:
                standalone.update(extra(self) or ())
        standalone.update({"images_to_pdf_ui", "office_to_pdf_ui",
                           "html_to_pdf_ui", "repair_pdf_ui"})
        return [(t, e, standalone) for t, e in groups]

    def _sidebar_group(self, parent, title, items, standalone=()):
        tk.Label(parent, text=title, font=FONT_SMALL, bg=SIDEBAR, fg=TEXT_MUTED,
                 anchor="w", padx=14, pady=6).pack(fill=tk.X)
        for text, cmd in items:
            item = SidebarItem(parent, text, cmd, False)
            item.needs_doc = getattr(cmd, "__name__", "") not in standalone
            item.pack(fill=tk.X, padx=6, pady=1)
            self.sidebar_items.append(item)

    # ---------------------------------------------------------------- preview
    def _build_preview(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.BOTH, expand=True)

        bar = tk.Frame(wrap, bg=SURFACE, highlightthickness=1,
                       highlightbackground=BORDER)
        bar.pack(side=tk.TOP, fill=tk.X)
        self.search_host = tk.Frame(bar, bg=SURFACE)
        self.search_host.pack(side=tk.TOP, fill=tk.X)

        tools = tk.Frame(bar, bg=SURFACE)
        tools.pack(side=tk.TOP, fill=tk.X, padx=10, pady=4)
        HoverButton(tools, "Search", command=self.find_text, small=True).pack(
            side=tk.LEFT, padx=(0, 6))
        HoverButton(tools, "Commands", command=self.show_palette, small=True).pack(
            side=tk.LEFT)
        HoverButton(tools, "+", command=lambda: self.zoom(1), small=True, padx=8,
                    tooltip="Zoom in (Ctrl++)").pack(side=tk.RIGHT, padx=2)
        self.zoom_label = tk.Label(tools, text="100%", font=FONT_SMALL, bg=SURFACE,
                                   fg=TEXT_MUTED, width=6)
        self.zoom_label.pack(side=tk.RIGHT)
        HoverButton(tools, "\u2212", command=lambda: self.zoom(-1), small=True,
                    padx=8, tooltip="Zoom out (Ctrl+-)").pack(side=tk.RIGHT, padx=2)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient=tk.VERTICAL, command=self._on_scroll,
                          bg=BORDER, troughcolor=BG, relief=tk.FLAT)
        self._preview_sb = sb
        self.canvas.configure(yscrollcommand=self._on_view_change)
        self.frame = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _on_view_change(self, first, last):
        """Scrollbar feedback hook - also the trigger for row virtualisation."""
        sb = getattr(self, "_preview_sb", None)
        if sb is not None:
            sb.set(first, last)
        self._schedule_visible_update()

    def _on_scroll(self, *args):
        self.canvas.yview(*args)
        self._schedule_visible_update()

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.window, width=event.width)
        self._sync_scrollregion()
        cols = self._columns_for(event.width)
        if cols != self._grid_cols:
            self._grid_cols = cols
            self._relayout_cards()
        self._schedule_visible_update()

    # -------------------------------------------------------------- statusbar
    def _build_status(self):
        self.status_bar = tk.Frame(self.root, bg=SURFACE, highlightthickness=1,
                                   highlightbackground=BORDER)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = tk.Label(self.status_bar, text="Open a PDF to start",
                                     font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED,
                                     anchor="w", padx=16, pady=6)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.busy_label = tk.Label(self.status_bar, text="", font=FONT_SMALL,
                                   bg=SURFACE, fg=ACCENT, padx=16)
        self.busy_label.pack(side=tk.RIGHT)

    def status(self, text, tone="info"):
        """One-line feedback.  ``tone``: info | accent | error | wait."""
        fg = {"error": DANGER, "accent": ACCENT, "wait": ACCENT}.get(tone, TEXT_MUTED)
        try:
            self.status_label.config(text=text, fg=fg)
        except tk.TclError:
            pass
        if tone == "wait":
            self._set_busy_message(text)
        elif tone != "wait":
            self._set_busy_message("")

    def _set_busy_message(self, message):
        try:
            self.busy_label.config(text=message)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ menus
    def _build_menu(self):
        menubar = tk.Menu(self.root, tearoff=0)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open\u2026", accelerator="Ctrl+O",
                              command=self.open_pdf)
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self.recent_menu)
        file_menu.add_command(label="Save As\u2026", accelerator="Ctrl+S",
                              command=self.save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Close Document", command=self.close_document)
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self._file_menu = file_menu

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Shift+Z",
                              command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", accelerator="Ctrl+A",
                              command=self.select_all)
        edit_menu.add_command(label="Clear Selection", accelerator="Escape",
                              command=self.clear_selection)
        edit_menu.add_separator()
        edit_menu.add_command(label="Find\u2026", accelerator="Ctrl+F",
                              command=self.find_text)
        edit_menu.add_command(label="Command Palette\u2026", accelerator="Ctrl+K",
                              command=self.show_palette)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        self._edit_menu = edit_menu

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Zoom In", accelerator="Ctrl++",
                              command=lambda: self.zoom(1))
        view_menu.add_command(label="Zoom Out", accelerator="Ctrl+-",
                              command=lambda: self.zoom(-1))
        view_menu.add_command(label="Actual Size", accelerator="Ctrl+0",
                              command=self.zoom_reset)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Keyboard Shortcuts",
                              command=self.show_shortcuts)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        menu.delete(0, tk.END)
        paths = self.recent.recent()
        if not paths:
            menu.add_command(label="(no recent files)", state=tk.DISABLED)
            return
        for path in paths[:10]:
            menu.add_command(label=os.path.basename(path),
                             command=lambda p=path: self.open_path(p))
        menu.add_separator()
        menu.add_command(label="Clear List", command=self._clear_recent)

    def _clear_recent(self):
        self.recent.clear_recent()
        self._refresh_recent_menu()

    # -------------------------------------------------------------- shortcuts
    # Widget classes that consume plain key/Control-key input themselves, so a
    # global shortcut must not steal Ctrl+A / Ctrl+Z while the user is typing.
    _TEXT_WIDGETS = ("Entry", "Text", "Spinbox", "TEntry", "TSpinbox", "TCombobox")

    def _focus_in_text(self):
        """True when the focused widget owns its own Ctrl-key editing."""
        try:
            widget = self.root.focus_get()
            klass = widget.winfo_class() if widget is not None else ""
        except (tk.TclError, KeyError):
            return False
        return klass in self._TEXT_WIDGETS

    def _select_text_in_focus(self, _event=None):
        """Ctrl+A inside a text field selects that field, not every page."""
        if not self._focus_in_text():
            return None
        try:
            self.root.focus_get().event_generate("<<SelectAll>>")
        except tk.TclError:
            pass
        return "break"

    def _guard_shortcut(self, handler):
        """Skip document shortcuts while a text field has focus."""
        def wrapped(event):
            if self._focus_in_text():
                return None
            return handler(event)
        return wrapped

    def _bind_shortcuts(self):
        bindings = {
            "<Control-o>": lambda e: self.open_pdf(),
            "<Control-s>": lambda e: self.save_as(),
            "<Control-Shift-S>": lambda e: self.save_as(),
            "<Control-z>": lambda e: self.undo(),
            "<Control-Shift-Z>": lambda e: self.redo(),
            "<Control-y>": lambda e: self.redo(),
            "<Control-a>": lambda e: self.select_all(),
            "<Control-f>": lambda e: self.find_text(),
            "<Control-k>": lambda e: self.show_palette(),
            "<Control-plus>": lambda e: self.zoom(1),
            "<Control-equal>": lambda e: self.zoom(1),
            "<Control-minus>": lambda e: self.zoom(-1),
            "<Control-0>": lambda e: self.zoom_reset(),
            "<Control-Shift-p>": lambda e: self.show_palette(),
            "<F3>": lambda e: self.find_text(),
            "<Escape>": self._on_escape,
            "<Control-q>": lambda e: self.on_close(),
        }
        for cls in type(self).__mro__:
            fn = cls.__dict__.get("shortcuts")
            if fn is None:
                continue
            for sequence, method in fn(self) or ():
                if sequence not in bindings:
                    bindings[sequence] = (
                        lambda m=method: getattr(self, m, lambda: None)())
        for sequence, handler in bindings.items():
            if sequence not in ("<Escape>", "<F3>"):
                handler = self._guard_shortcut(handler)
            self.root.bind_all(sequence, handler, add="+")
        # Runs after the guarded handler, so Enter/Text widgets get a real
        # select-all while the page grid still gets one when it has focus.
        self.root.bind_all("<Control-a>", self._select_text_in_focus, add="+")

    def _on_escape(self, _event=None):
        if self._search_panel is not None and self._search_panel.winfo_exists():
            self._search_panel.close()
            self._search_panel = None
            return "break"
        self.clear_selection()
        return None

    # ------------------------------------------------------------ app actions
    def undo(self):
        label = self.tool.undo()
        if not label:
            self.status("Nothing to undo")
            return
        self._after_document_change()
        self.status(f"Undid: {label}", "accent")

    def redo(self):
        label = self.tool.redo()
        if not label:
            self.status("Nothing to redo")
            return
        self._after_document_change()
        self.status(f"Redid: {label}", "accent")

    def clear_selection(self):
        if not self.tool.doc:
            return
        self.tool.clear_selection()
        self._refresh_selection_styles()
        self._refresh_status()

    def zoom(self, direction):
        target = max(0, min(len(ZOOM_STEPS) - 1, self.zoom_index + direction))
        if target == self.zoom_index:
            return
        self.zoom_index = target
        self._relayout_cards()

    def zoom_reset(self):
        if self.zoom_index == 2:
            return
        self.zoom_index = 2
        self._relayout_cards()

    @property
    def thumb_width(self):
        return ZOOM_STEPS[self.zoom_index]

    def find_text(self):
        if self._search_panel is not None and self._search_panel.winfo_exists():
            self._search_panel.focus_entry()
            return
        if not self._require_doc():
            return
        self._search_panel = SearchPanel(self.search_host, self)
        self._search_panel.pack(fill=tk.X)
        self._search_panel.focus_entry()

    def show_palette(self):
        if self._palette is None:
            self._palette = CommandPalette(self, self._commands())
        else:
            self._palette.commands = self._commands()
        self._palette.open()

    def _commands(self):
        """Every sidebar action + shell action, as (title, callable, shortcut)."""
        commands = [(item.text, item.command, "") for item in self.sidebar_items]
        commands += [
            ("Undo", self.undo, "Ctrl+Z"),
            ("Redo", self.redo, "Ctrl+Shift+Z"),
            ("Save As", self.save_as, "Ctrl+S"),
            ("Open PDF", self.open_pdf, "Ctrl+O"),
            ("Find in Document", self.find_text, "Ctrl+F"),
            ("Select All Pages", self.select_all, "Ctrl+A"),
            ("Clear Selection", self.clear_selection, "Esc"),
            ("Zoom In", lambda: self.zoom(1), "Ctrl++"),
            ("Zoom Out", lambda: self.zoom(-1), "Ctrl+-"),
            ("Actual Size", self.zoom_reset, "Ctrl+0"),
            ("Keyboard Shortcuts", self.show_shortcuts, ""),
            ("Close Document", self.close_document, ""),
        ]
        return commands

    def show_shortcuts(self):
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "Ctrl+O  Open PDF\n"
            "Ctrl+S  Save As\n"
            "Ctrl+Z / Ctrl+Shift+Z  Undo / Redo\n"
            "Ctrl+A  Select all pages\n"
            "Ctrl+F  Find in document\n"
            "Ctrl+K  Command palette\n"
            "Ctrl++ / Ctrl+- / Ctrl+0  Zoom\n"
            "Shift+Click  Select a page range\n"
            "Escape  Close search / clear selection",
            parent=self.root)

    def show_about(self):
        messagebox.showinfo(
            "About PDF Tool",
            "PDF Tool\nTkinter + PyMuPDF desktop PDF editor.\n"
            "Undo history, background jobs, lazy page rendering.",
            parent=self.root)

    def close_document(self):
        if not self._guard_unsaved():
            return
        self.tool.close()
        self.thumbs.begin()
        self._cards.clear()
        for w in self.frame.winfo_children():
            w.destroy()
        self.file_label.config(text="No file open")
        self.root.title("PDF Tool")
        self._sync_enabled()
        self._refresh_status()
        self.status("Document closed")

    def on_close(self):
        if self._closing:
            return
        if not self._guard_unsaved():
            return
        self._closing = True
        try:
            self.jobs.shutdown()
            self.thumbs.stop()
        finally:
            self.root.destroy()

    # --------------------------------------------------------------- opening
    def open_pdf(self):
        if not self._guard_unsaved():
            return
        from .dialogs import native_open_file
        path = native_open_file("Select PDF")
        if path:
            self.open_path(path)

    def open_path(self, path, password=None):
        if not os.path.isfile(path):
            messagebox.showerror("PDF Tool", f"File not found:\n{path}",
                                 parent=self.root)
            self._refresh_recent_menu()
            return
        if not self._guard_unsaved():
            return
        for attempt in range(3):
            try:
                self.tool.open(path, password)
                break
            except ValueError as e:
                if str(e) == "NEEDS_PASSWORD":
                    from tkinter import simpledialog
                    password = simpledialog.askstring(
                        "Password",
                        "This PDF is password protected.\nEnter password"
                        + (f" (attempt {attempt + 1}/3)" if attempt else "") + ":",
                        show="*", parent=self.root)
                    if password is None:
                        return
                else:
                    messagebox.showerror("PDF Tool", str(e), parent=self.root)
                    return
        else:
            messagebox.showerror("PDF Tool", "Incorrect password.", parent=self.root)
            return
        self._after_document_change()
        self.recent.add_recent(path)
        self._refresh_recent_menu()
        self.root.title(f"PDF Tool \u2014 {os.path.basename(path)}")
        self.status(f"Opened {os.path.basename(path)} \u2014 {self.tool.page_count} pages",
                    "accent")

    def _after_document_change(self):
        """Invalidate caches and repaint after open/undo/worker result."""
        self.thumbs.begin()
        self._thumb_requests.clear()
        self._sync_enabled()
        self._refresh_preview()

    def _guard_unsaved(self):
        """True if it is safe to discard the in-memory document."""
        if not self.tool.doc or not self.tool.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            "This document has unsaved changes.\n\nSave before continuing?",
            parent=self.root)
        if answer is None:
            return False
        if answer:
            return bool(self.save_as())
        return True

    def save_as(self):
        from .dialogs import native_save_file
        if not self.tool.doc:
            messagebox.showinfo("PDF Tool", "Open a PDF first.", parent=self.root)
            return False
        default = os.path.basename(self.tool.path or "document.pdf")
        path = native_save_file("Save PDF As", initial=default)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self.tool.save_as(path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self.root)
            return False
        self.tool.mark_clean()
        self.recent.add_recent(path)
        self._refresh_recent_menu()
        self.file_label.config(text=os.path.basename(path))
        self.root.title(f"PDF Tool \u2014 {os.path.basename(path)}")
        self.status(f"Saved to {path}", "accent")
        self._refresh_status()
        return True

    # --------------------------------------------------------------- helpers
    def _sync_enabled(self):
        has = self.tool.doc is not None
        for btn in (getattr(self, "btn_save", None), getattr(self, "btn_open", None)):
            if btn is not None:
                btn.set_enabled(has if btn is self.btn_save else True)
        self.btn_undo.set_enabled(self.tool.can_undo)
        self.btn_redo.set_enabled(self.tool.can_redo)
        for item in self.sidebar_items:
            item.set_enabled(has or not getattr(item, "needs_doc", True))

    def _refresh_status(self):
        if not self.tool.doc:
            self.file_label.config(text="No file open")
            return
        name = os.path.basename(self.tool.path or "untitled.pdf")
        sel = self.tool.selection
        dots = []
        if self.tool.dirty:
            dots.append("unsaved")
        if self.tool.can_undo:
            dots.append(f"undo: {self.tool.undo_label}")
        suffix = ("  \u00b7  " + "  \u00b7  ".join(dots)) if dots else ""
        self.file_label.config(text=f"{name}  \u00b7  {self.tool.page_count} pages{suffix}")
        if sel:
            shown = ", ".join(str(i + 1) for i in sel[:15])
            more = f" \u2026 (+{len(sel) - 15})" if len(sel) > 15 else ""
            self.status_label.config(
                text=f"Selected {len(sel)} pages:  {shown}{more}", fg=ACCENT)
        else:
            self.status_label.config(text="Click pages to select  \u00b7  "
                                          "Shift+click for a range", fg=TEXT_MUTED)

    def _require_doc(self):
        if not self.tool.doc:
            messagebox.showinfo("PDF Tool", "Open a PDF first.", parent=self.root)
            return False
        return True

    def _set_busy(self, busy, message=""):
        try:
            self.root.config(cursor="watch" if busy else "")
        except tk.TclError:
            pass
        self._set_busy_message(message or ("Working\u2026" if busy else ""))

    def _apply_pdf_bytes(self, data, label="Edit"):
        """Replace the open document with ``data`` as a single undo step."""
        if not self.tool.doc:
            return False
        self.tool.checkpoint(label)
        try:
            self.tool.replace_document(data, label)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return False
        self._after_document_change()
        self.status(label, "accent")
        return True

    def run_job(self, title, fn, on_done=None, on_error=None, on_progress=None,
                name="pdf-task", cancellable=True):
        """Submit ``fn(job)`` to the background runner (see :mod:`pdftool.jobs`)."""
        return run_job(self, title, fn, on_done=on_done, on_error=on_error,
                       on_progress=on_progress, name=name, cancellable=cancellable)

    def snapshot_bytes(self):
        """Current document as bytes, safe to hand to a worker thread."""
        return self.tool.snapshot_bytes()

    def detached_tool(self):
        """A :class:`PDFTool` copy of the open document, safe on a worker.

        The copy keeps the source file name so exported files stay named after
        the document the user actually opened.
        """
        return PDFTool.from_bytes(self.tool.snapshot_bytes(),
                                  source_name=self.tool.source_name)

    def mutate_in_job(self, title, mutate, label=None, on_done=None,
                      name="mutate", cancellable=True):
        """Apply ``mutate(tool, job)`` to a byte copy on a worker thread.

        ``mutate`` receives a detached :class:`pdf_ops.PDFTool` (never the live
        one) and may return anything picklable.  Its document becomes a single
        undo step via :meth:`_apply_pdf_bytes`; widgets are only touched on the
        Tk thread, so dialogs stay alive while the work runs.
        """
        if not self.tool.doc:
            return None
        label = label or title
        source_name = self.tool.source_name
        try:
            snap = self.tool.snapshot_bytes()
        except Exception as e:
            messagebox.showerror(title, f"Could not read the document: {e}",
                                 parent=self.root)
            return None

        def work(job):
            tool = PDFTool.from_bytes(snap, source_name=source_name)
            try:
                result = mutate(tool, job)
                data = tool.to_bytes()
            finally:
                tool.close()
            return data, result

        def done(payload):
            data, result = payload
            if not self._apply_pdf_bytes(data, label):
                return
            if on_done:
                on_done(result)

        return run_job(self, title, work, on_done=done, name=name,
                       cancellable=cancellable)

    # ------------------------------------------------------------------ grid
    def _columns_for(self, width):
        return max(1, int((max(int(width), 120) - 2 * CARD_PAD) //
                          (self.thumb_width + 2 * CARD_PAD)))

    def _refresh_preview(self):
        for w in self.frame.winfo_children():
            w.destroy()
        self._spare_cards = []
        self._cards = {}
        self._region_key = None
        self._measured_row_h = 0      # zoom changes the row height
        self.zoom_label.config(text=f"{int(self.thumb_width / 1.4)}%")
        self._sync_enabled()
        self._refresh_status()
        if not self.tool.doc:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            self.canvas.yview_moveto(0)
            return
        self._grid_cols = self._columns_for(self.canvas.winfo_width() or 900)
        self.canvas.yview_moveto(0)
        self._update_visible()

    def _relayout_cards(self):
        """Zoom change: drop rendered cards, keep selection, rebuild lazily."""
        self._refresh_preview()

    @property
    def _row_h(self):
        """Height of one grid row in pixels (card + its padding).

        Measured from a live card when one exists, otherwise derived from the
        widget metrics so the scrollbar is correct before anything is built.
        """
        if self._measured_row_h:
            return self._measured_row_h
        try:
            label_h = tkfont.Font(font=FONT_SMALL).metrics("linespace") + 4
        except tk.TclError:
            label_h = 20
        return self.thumb_width + 12 + label_h + 2 * CARD_PAD

    def _total_rows(self):
        if not self.tool.doc:
            return 0
        return (self.tool.page_count + self._grid_cols - 1) // self._grid_cols

    def _visible_rows(self):
        """``(first, last)`` row indexes to keep built, one row of overscan."""
        rows = self._total_rows()
        if not rows:
            return 0, -1
        row_h = self._row_h
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height() or 700
        first = max(0, int(top // row_h) - 1)
        last = min(rows - 1, int((top + height) // row_h) + 1)
        return first, last

    def _ensure_rows(self, first, last):
        """Create cards for rows in range, destroy cards outside it."""
        cols = self._grid_cols
        total = self.tool.page_count
        for index in [i for i in self._cards if not (first <= i // cols <= last)]:
            entry = self._cards.pop(index, None)
            self._thumb_requests.discard(index)
            if entry:
                entry["frame"].grid_remove()
                if len(self._spare_cards) < self._grid_cols * 4:
                    self._spare_cards.append(entry)
                else:
                    entry["frame"].destroy()
        for row in range(first, last + 1):
            for col in range(cols):
                index = row * cols + col
                if index >= total:
                    break
                if index in self._cards:
                    continue
                if self._spare_cards:
                    self._recycle_card(self._spare_cards.pop(), index, cols)
                else:
                    self._build_card(index, cols)
        self._sync_scrollregion()

    def _sync_scrollregion(self):
        """Reserve full height for unbuilt rows so the scrollbar never jumps."""
        rows = self._total_rows()
        row_h = self._row_h
        width = max(1, self.canvas.winfo_width())
        key = (rows, row_h, width)
        if key == self._region_key:
            return
        self._region_key = key
        for row in range(rows):
            self.frame.grid_rowconfigure(row, minsize=row_h)
        self.canvas.configure(scrollregion=(0, 0, width, rows * row_h))
        self.canvas.itemconfig(self.window, height=max(rows * row_h, 1))

    def _measure_row(self):
        """Learn the real row height from a built card, once per zoom level."""
        for entry in self._cards.values():
            try:
                height = entry["frame"].winfo_reqheight()
            except tk.TclError:
                continue
            if height > 1:
                self._measured_row_h = height + 2 * CARD_PAD
                return True
        return False

    def _build_card(self, index, cols):
        selected = index in self.tool.selection
        border = ACCENT if selected else BORDER
        card = tk.Frame(self.frame, bg=SURFACE, highlightthickness=2,
                        highlightbackground=border, highlightcolor=border,
                        cursor="hand2")
        card.grid(row=index // cols, column=index % cols, padx=CARD_PAD,
                  pady=CARD_PAD, sticky="n")
        card.config(padx=6, pady=6)

        holder = tk.Canvas(card, width=self.thumb_width, height=self.thumb_width,
                           bg=ACCENT_SOFT, highlightthickness=0, cursor="hand2")
        holder.pack()
        placeholder = holder.create_text(self.thumb_width // 2,
                                         self.thumb_width // 2,
                                         text=f"Page {index + 1}",
                                         fill=TEXT_MUTED, font=FONT_SMALL)

        num = tk.Label(card, text=str(index + 1),
                       font=FONT_BOLD if selected else FONT_SMALL,
                       bg=ACCENT_SOFT if selected else SURFACE,
                       fg=ACCENT if selected else TEXT_MUTED, padx=6, pady=2)
        num.pack(fill=tk.X)

        for w in (card, holder, num):
            w.bind("<Button-1>", lambda e, i=index: self._toggle(i, e))
            w.bind("<Enter>", lambda e, c=card: self._card_hover(c, True))
            w.bind("<Leave>", lambda e, c=card: self._card_hover(c, False))

        entry = {"frame": card, "canvas": holder, "placeholder": placeholder,
                 "num": num, "photo": None, "index": index}
        self._cards[index] = entry
        self._request_thumb(index)
        return entry

    def _recycle_card(self, entry, index, cols):
        """Re-point an existing card at another page (no widget churn)."""
        selected = index in self.tool.selection
        border = ACCENT if selected else BORDER
        frame = entry["frame"]
        frame.grid_configure(row=index // cols, column=index % cols)
        frame.config(highlightbackground=border, highlightcolor=border)
        entry["num"].config(text=str(index + 1),
                            font=FONT_BOLD if selected else FONT_SMALL,
                            bg=ACCENT_SOFT if selected else SURFACE,
                            fg=ACCENT if selected else TEXT_MUTED)
        holder = entry["canvas"]
        holder.delete("all")
        holder.config(width=self.thumb_width, height=self.thumb_width, bg=ACCENT_SOFT)
        entry["placeholder"] = holder.create_text(
            self.thumb_width // 2, self.thumb_width // 2,
            text=f"Page {index + 1}", fill=TEXT_MUTED, font=FONT_SMALL)
        entry["photo"] = None
        entry["index"] = index
        for widget in (frame, holder, entry["num"]):
            widget.bind("<Button-1>", lambda e, i=index: self._toggle(i, e))
            widget.bind("<Enter>", lambda e, c=frame: self._card_hover(c, True))
            widget.bind("<Leave>", lambda e, c=frame: self._card_hover(c, False))
        self._cards[index] = entry
        self._request_thumb(index)
        return entry

    def _card_hover(self, card, entering):
        if card.cget("highlightbackground") != ACCENT:
            color = ACCENT_SOFT if entering else BORDER
            card.config(highlightbackground=color, highlightcolor=color)

    def _request_thumb(self, index):
        if index in self._thumb_requests:
            return
        entry = self._cards.get(index)
        if entry is None or entry.get("photo") is not None:
            return
        self._thumb_requests.add(index)
        width = self.thumb_width

        def deliver(idx, ppm, w, h):
            self._thumb_requests.discard(idx)
            card = self._cards.get(idx)
            if not card or width != self.thumb_width:
                return
            try:
                photo = tk.PhotoImage(data=ppm, width=w, height=h)
            except Exception:
                return
            canvas = card["canvas"]
            canvas.delete("all")
            canvas.config(width=w, height=h, bg=SURFACE)
            canvas.create_image(w // 2, h // 2, image=photo)
            card["photo"] = photo       # keep a reference alive
            card["canvas_size"] = (w, h)
            self._schedule_visible_update()

        self.thumbs.request(self.tool.doc, index, deliver,
                            lock=getattr(self.tool, "_lock", None), width=width)

    def _schedule_visible_update(self):
        if self._visible_job is not None:
            try:
                self.root.after_cancel(self._visible_job)
            except Exception:
                pass
        self._visible_job = self.root.after(30, self._update_visible)

    def _update_visible(self):
        self._visible_job = None
        if not self.tool.doc:
            return
        first, last = self._visible_rows()
        if last < first:
            return
        if not self._measured_row_h and self._measure_row():
            self._sync_scrollregion()
            first, last = self._visible_rows()
        self._ensure_rows(first, last)
        for index in range(first * self._grid_cols,
                           min((last + 1) * self._grid_cols,
                               self.tool.page_count)):
            self._request_thumb(index)

    def _toggle(self, index, event=None):
        if not self.tool.doc:
            return
        if event is not None and (event.state & 0x0001):
            # Shift = extend the selection to a contiguous range.
            anchor = min(self.tool.selection) if self.tool.selection else index
            lo, hi = sorted((anchor, index))
            for i in range(lo, hi + 1):
                if i not in self.tool.selection:
                    self.tool.toggle_select(i)
        else:
            self.tool.toggle_select(index)
        self._refresh_selection_styles()
        self._refresh_status()

    def _refresh_selection_styles(self):
        for index, card in self._cards.items():
            if not card:
                continue
            selected = index in self.tool.selection
            border = ACCENT if selected else BORDER
            card["frame"].config(highlightbackground=border, highlightcolor=border)
            card["num"].config(text=str(index + 1),
                               font=FONT_BOLD if selected else FONT_SMALL,
                               bg=ACCENT_SOFT if selected else SURFACE,
                               fg=ACCENT if selected else TEXT_MUTED)

    def _show_hit(self, hit):
        """Scroll to a search hit's page and flash its card."""
        index = int(hit.get("page", 0))
        snippet = " ".join((hit.get("line") or hit.get("text") or "").split())
        if not self.tool.doc or not (0 <= index < self.tool.page_count):
            return
        # Rows outside the viewport are not built, so scroll by arithmetic
        # first and let the virtualizer materialise the target card.
        total_h = max(1, self._total_rows() * self._row_h)
        view_h = self.canvas.winfo_height() or 700
        row_px = (index // self._grid_cols) * self._row_h - view_h / 3.0
        span = max(1, total_h - view_h)
        self.canvas.yview_moveto(max(0.0, min(1.0, row_px / span)))
        self._update_visible()
        self.canvas.update_idletasks()
        card = self._cards.get(index)
        self.status(f"Page {index + 1}: {snippet}", "accent")
        if card is None:
            return
        frame = card["frame"]
        frame.config(highlightbackground=DANGER, highlightcolor=DANGER)

        def restore():
            try:
                selected = index in self.tool.selection
                color = ACCENT if selected else BORDER
                frame.config(highlightbackground=color, highlightcolor=color)
            except tk.TclError:
                pass          # card was rebuilt/destroyed while flashing

        self.root.after(1200, restore)
        self.status(f"Page {index + 1}: {snippet}", "accent")


def main(argv=None):
    """Launch the app, optionally opening a PDF passed on the command line."""
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    from .dnd import create_root
    root = create_root()
    app = PDFToolApp(root)
    if argv:
        path = argv[0]
        root.after(60, lambda: app.open_path(path))
    root.mainloop()


if __name__ == "__main__":
    main()