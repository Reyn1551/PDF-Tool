"""In-document text search panel.

Searching a 500-page PDF on the main thread freezes the window, so anything
bigger than :data:`INLINE_PAGE_LIMIT` pages runs on a worker thread against a
byte snapshot; the hits are then navigated with Enter / Shift+Enter.
"""
import tkinter as tk

import fitz

from .theme import (
    SURFACE, BORDER, TEXT, TEXT_MUTED, ACCENT, DANGER, FONT_SMALL,
)
from .widgets import HoverButton
from .jobs import run_job

INLINE_PAGE_LIMIT = 40      # pages searched synchronously
PLACEHOLDER = "Search text"
MAX_LISTED_HITS = 5000


def parse_page_spec(spec, total):
    """``"1-3,7"`` -> ``[0,1,2,6]``; blank -> ``None`` (all pages).

    Raises :class:`ValueError` for malformed or out-of-range input.
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2:
                raise ValueError(f"Invalid range '{part}'.")
            try:
                first, last = int(bits[0]), int(bits[1])
            except ValueError:
                raise ValueError(f"Invalid range '{part}'.")
            if first < 1 or last > total or first > last:
                raise ValueError(f"Range '{part}' is outside 1-{total}.")
            out.extend(range(first - 1, last))
        else:
            try:
                page = int(part)
            except ValueError:
                raise ValueError(f"Invalid page '{part}'.")
            if page < 1 or page > total:
                raise ValueError(f"Page {page} is outside 1-{total}.")
            out.append(page - 1)
    if not out:
        raise ValueError("No pages selected.")
    return sorted(set(out))


def _line_text(page, rect, pad=2.0):
    """Text on the visual line that contains ``rect`` (for the hit list)."""
    band = fitz.Rect(page.rect.x0, rect.y0 - pad, page.rect.x1, rect.y1 + pad)
    text = " ".join(page.get_text("text", clip=band).split())
    return text or " ".join(page.get_text("text", clip=rect).split())


def _norm(text):
    return " ".join(text.split())


def search_pdf(doc, needle, pages=None, limit=MAX_LISTED_HITS, case_sensitive=False):
    """Find ``needle`` in an open document.

    Same result shape as ``PDFTool.search_text``; ``case_sensitive`` is applied
    by re-reading the text under each hit rectangle, because MuPDF's
    ``search_for`` ignores case.
    """
    if pages is None:
        pages = range(doc.page_count)
    wanted = _norm(needle)
    hits = []
    for pno in pages:
        if pno < 0 or pno >= doc.page_count:
            continue
        page = doc.load_page(pno)
        for rect in page.search_for(needle):
            if case_sensitive and wanted not in _norm(page.get_text("text", clip=rect)):
                continue
            hits.append({"page": pno, "rect": fitz.Rect(rect), "text": needle,
                         "line": _line_text(page, rect)})
            if len(hits) >= limit:
                return hits
    return hits


class SearchPanel(tk.Frame):
    """Search bar shown above the page grid."""

    def __init__(self, master, app):
        super().__init__(master, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER)
        self.app = app
        self._hits = []
        self._cursor = -1
        self._query = ""
        self._showing_placeholder = False

        self.entry = tk.Entry(self, width=28, font=FONT_SMALL, relief=tk.FLAT,
                              highlightthickness=1, highlightbackground=BORDER,
                              bg=SURFACE, fg=TEXT, insertbackground=TEXT)
        self.entry.pack(side=tk.LEFT, padx=(10, 6), pady=6)
        self._set_placeholder()

        tk.Label(self, text="pages", font=FONT_SMALL, bg=SURFACE,
                 fg=TEXT_MUTED).pack(side=tk.LEFT)
        self.range_var = tk.StringVar()
        self.range_entry = tk.Entry(self, textvariable=self.range_var, width=9,
                                    font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1,
                                    highlightbackground=BORDER, bg=SURFACE, fg=TEXT,
                                    insertbackground=TEXT)
        self.range_entry.pack(side=tk.LEFT, padx=(6, 10), pady=6)

        self.case_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Aa", variable=self.case_var, font=FONT_SMALL, bg=SURFACE,
                       fg=TEXT_MUTED, activebackground=SURFACE, selectcolor=SURFACE,
                       relief=tk.FLAT, highlightthickness=0,
                       command=self._reset).pack(side=tk.LEFT, padx=(0, 8))

        HoverButton(self, "\u2039", command=self.prev, small=True, padx=8,
                    pady=2).pack(side=tk.LEFT, padx=(0, 2))
        HoverButton(self, "\u203a", command=self.next, small=True, padx=8,
                    pady=2).pack(side=tk.LEFT, padx=(0, 8))

        self.counter = tk.Label(self, text="0 / 0", font=FONT_SMALL, bg=SURFACE,
                                fg=TEXT_MUTED, width=14)
        self.counter.pack(side=tk.LEFT)

        HoverButton(self, "\u00d7", command=self.close, small=True, padx=8,
                    pady=2).pack(side=tk.RIGHT, padx=(6, 10))

        self.entry.bind("<Return>", lambda e: self.search())
        self.entry.bind("<Shift-Return>", lambda e: self.prev())
        self.entry.bind("<Escape>", lambda e: self.close())
        self.range_entry.bind("<Return>", lambda e: self.search())
        self.range_entry.bind("<Escape>", lambda e: self.close())
        self.bind("<Destroy>", lambda e: self._hits.clear())

    # ---------- placeholder ----------

    def _set_placeholder(self):
        self._showing_placeholder = True
        self.entry.delete(0, tk.END)
        self.entry.insert(0, PLACEHOLDER)
        self.entry.config(fg=TEXT_MUTED)
        self.entry.bind("<FocusIn>", self._clear_placeholder)

    def _clear_placeholder(self, _event=None):
        if self._showing_placeholder:
            self.entry.delete(0, tk.END)
            self.entry.config(fg=TEXT)
            self._showing_placeholder = False

    def _query_text(self):
        return "" if self._showing_placeholder else self.entry.get().strip()

    # ---------- lifecycle ----------

    def focus_entry(self):
        self._clear_placeholder()
        self.entry.focus_set()
        self.entry.select_range(0, tk.END)

    def close(self):
        if not self.winfo_exists():
            return
        self.destroy()
        refresh = getattr(self.app, "_refresh_preview", None)
        if refresh:
            refresh()

    def _reset(self):
        self._hits = []
        self._cursor = -1
        self.counter.config(text="0 / 0", fg=TEXT_MUTED)
        self.range_entry.config(highlightbackground=BORDER, highlightcolor=BORDER)

    # ---------- searching ----------

    def search(self):
        needle = self._query_text()
        if not needle:
            self._reset()
            return
        total = self.app.tool.page_count
        try:
            pages = parse_page_spec(self.range_var.get(), total)
        except ValueError:
            self.range_entry.config(highlightbackground=DANGER, highlightcolor=DANGER)
            self.counter.config(text="bad range", fg=DANGER)
            return
        self.range_entry.config(highlightbackground=BORDER, highlightcolor=BORDER)
        self._reset()
        self._query = needle
        self.counter.config(text="searching...")

        if total <= INLINE_PAGE_LIMIT:
            self._apply(self.app.tool.search_text(needle, pages))
            return

        snap = self.app.tool.snapshot_bytes()
        case_sensitive = self.case_var.get()

        def work(job):
            doc = fitz.open(stream=snap, filetype="pdf")
            try:
                return search_pdf(doc, needle, pages, case_sensitive=case_sensitive)
            finally:
                doc.close()

        run_job(self.app, "Searching document", work, on_done=self._apply,
                name="search", cancellable=True)

    def _apply(self, hits):
        self._hits = list(hits)
        self._cursor = -1
        if not self._hits:
            self.counter.config(text="0 / 0", fg=TEXT_MUTED)
            self.app.status(f"No matches for \u201c{self._query}\u201d")
            return
        self.counter.config(text=f"0 / {len(self._hits)}", fg=ACCENT)
        self.app.status(f"{len(self._hits)} match(es) for \u201c{self._query}\u201d")
        self.next()

    # ---------- navigation ----------

    def _goto(self, index):
        hit = self._hits[index]
        self._cursor = index
        self.counter.config(text=f"{index + 1} / {len(self._hits)}", fg=ACCENT)
        show = getattr(self.app, "_show_hit", None)
        if show:
            show(hit)
        else:
            snippet = " ".join((hit.get("line") or hit.get("text") or "").split())
            self.app.status(f"Page {hit['page'] + 1}: {snippet}")

    def next(self):
        if not self._hits:
            self.search()
            return
        self._goto((self._cursor + 1) % len(self._hits))

    def prev(self):
        if not self._hits:
            return
        self._goto((self._cursor - 1) % len(self._hits))