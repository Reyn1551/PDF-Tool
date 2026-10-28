"""Advanced conversions: Markdown, SVG, web optimisation, batch folders.

Everything here is bytes-in / bytes-out and runs on the shared job runner, so
converting a 900-page catalogue never blocks the page grid.
"""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

from .dialogs import native_open_dir, native_save_file_any
from .jobs import progress_bridge
from .theme import BG, TEXT, TEXT_MUTED, BORDER, SURFACE, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, _safe_grab
from pdf_convert_extra import (
    batch_convert, page_to_svg, scan_folder, stats, to_markdown, web_optimize,
)

BATCH_KINDS = [
    ("PDF \u2192 Markdown", "pdf_to_markdown", ".pdf"),
    ("PDF \u2192 Text", "pdf_to_text", ".pdf"),
    ("PDF \u2192 Images (PNG)", "pdf_to_images", ".pdf"),
    ("PDF \u2192 Word", "pdf_to_word", ".pdf"),
    ("Images \u2192 PDF", "image_to_pdf", ".png.jpg.jpeg"),
    ("Office \u2192 PDF", "office_to_pdf", ".docx.docx.xlsx.pptx.odt.rtf"),
]


class ConvertExtraActions:
    """Mixin - combined into PDFToolApp via multiple inheritance."""

    # ---------- registry ----------

    def sidebar_entries(self):
        return [("ADVANCED CONVERT", [
            ("PDF \u2192 Markdown", "markdown_ui"),
            ("Page \u2192 SVG", "svg_ui"),
            ("Web Optimize", "web_optimize_ui"),
            ("Batch Convert Folder", "batch_ui"),
            ("Word & Image Counts", "counts_ui"),
        ])]

    def shortcuts(self):
        return [("<Control-Shift-m>", "markdown_ui")]

    def standalone_methods(self):
        return {"batch_ui"}

    # ---------- helpers ----------

    def _stem(self):
        return os.path.splitext(os.path.basename(self.tool.path))[0] if self.tool.path else "document"

    def _run_bytes_job(self, title, fn, name):
        """Run ``fn(bytes) -> result`` against a snapshot on the worker."""
        snap = self.snapshot_bytes()

        def work(job):
            job.progress("Working\u2026", 0.2)
            return fn(snap)

        return self.run_job(title, work, name=name)

    # ---------- Markdown ----------

    def markdown_ui(self):
        if not self._require_doc():
            return
        out = native_save_file_any("Save Markdown", initial=self._stem() + ".md")
        if not out:
            return

        def work(job):
            data = self.snapshot_bytes()
            job.progress("Reading structure\u2026", 0.3)
            md = to_markdown(data)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(md)
            return len(md)

        def done(chars):
            messagebox.showinfo("PDF \u2192 Markdown",
                                f"{chars:,} characters written to:\n{out}",
                                parent=self.root)

        self.run_job("PDF \u2192 Markdown", work, on_done=done, name="markdown")

    # ---------- SVG ----------

    def svg_ui(self):
        if not self._require_doc():
            return
        pages = sorted(self.tool.selection) or [0]
        if len(pages) > 1:
            page_no = simpledialog.askinteger(
                "Page \u2192 SVG", f"Which page? (1-{self.tool.page_count})",
                initialvalue=pages[0] + 1, minvalue=1,
                maxvalue=self.tool.page_count, parent=self.root)
            if page_no is None:
                return
            index = page_no - 1
        else:
            index = pages[0]
        out = native_save_file_any("Save SVG", initial=f"{self._stem()}_page-{index + 1}.svg")
        if not out:
            return

        def work(job):
            data = self.snapshot_bytes()
            job.progress("Vectorising\u2026", 0.4)
            svg = page_to_svg(data, index, text_as_path=True)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(svg)
            return len(svg)

        def done(size):
            messagebox.showinfo("Page \u2192 SVG",
                                f"Page {index + 1} exported ({size / 1024:.0f} KB):\n{out}",
                                parent=self.root)

        self.run_job("Page \u2192 SVG", work, on_done=done, name="svg")

    # ---------- web optimisation ----------

    def web_optimize_ui(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Web Optimize")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)

        tk.Label(dlg, text="Rewrite for fast download (smaller, clean streams).",
                 font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(14, 4))
        split_var = tk.StringVar(value="0")
        tk.Label(dlg, text="Split into chunks of N pages (0 = single file):",
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16)
        entry = tk.Entry(dlg, textvariable=split_var, width=8, font=FONT,
                         relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry.pack(anchor="w", padx=16, pady=(2, 12))

        def start():
            try:
                step = max(0, int(split_var.get() or 0))
            except ValueError:
                messagebox.showerror("Web Optimize", "Chunk size must be a number.", parent=dlg)
                return
            dlg.destroy()
            target = native_open_dir("Destination folder")
            if not target:
                return
            self._web_optimize(target, step)

    def _web_optimize(self, target, step=0):
        """Write web-optimised file(s) for the document into ``target``."""
        stem = self._stem()
        snap = self.snapshot_bytes()

        def work(job):
            job.progress("Optimising\u2026", 0.4)
            result = web_optimize(snap, subdivide_pages=step or None)
            if isinstance(result, list):
                written = []
                for label, chunk in result:
                    path = os.path.join(target, f"{stem}_{label}.pdf")
                    with open(path, "wb") as fh:
                        fh.write(chunk)
                    written.append(path)
                return written
            path = os.path.join(target, f"{stem}_web.pdf")
            with open(path, "wb") as fh:
                fh.write(result)
            return [path]

        def done(written):
            total = sum(os.path.getsize(p) for p in written)
            messagebox.showinfo(
                "Web Optimize",
                f"{len(written)} file(s), {total / 1024 / 1024:.2f} MB total:\n{target}",
                parent=self.root)

        return self.run_job("Optimising for web", work, on_done=done,
                            name="web-optimize")

        row = tk.Frame(dlg, bg=BG)
        row.pack(pady=(0, 14))
        HoverButton(row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(row, "Optimize", command=start, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- batch folder ----------

    def batch_ui(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Batch Convert")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)

        tk.Label(dlg, text="Convert every file in a folder, one failure never stops the run.",
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w",
                 wraplength=380, justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(14, 6))

        kind_var = tk.StringVar(value=BATCH_KINDS[0][1])
        for label, key, _ext in BATCH_KINDS:
            tk.Radiobutton(dlg, text=label, variable=kind_var, value=key, bg=BG, fg=TEXT,
                           font=FONT, activebackground=BG,
                           selectcolor=SURFACE).pack(anchor="w", padx=24, pady=1)

        def start():
            kind = kind_var.get()
            ext = next(e for _l, k, e in BATCH_KINDS if k == kind)
            dlg.destroy()
            folder = native_open_dir("Folder to convert")
            if not folder:
                return
            out_dir = native_open_dir("Destination folder")
            if not out_dir:
                return
            self._batch_convert(folder, out_dir, kind, ext)

    def _batch_convert(self, folder, out_dir, kind, ext):
        """Convert every matching file in ``folder`` into ``out_dir``."""
        files = scan_folder(folder, [e for e in ext.split(".") if e])
        if not files:
            messagebox.showinfo("Batch Convert", "No matching files in that folder.",
                                parent=self.root)
            return None

        def work(job):
            return batch_convert(files, out_dir, kind,
                                 progress=progress_bridge(job, "Converted"),
                                 should_stop=lambda: job.cancel_requested)

        def done(result):
            lines = [f"{len(result['ok'])} converted"]
            if result["failed"]:
                lines.append(f"{len(result['failed'])} failed")
            if result["skipped"]:
                lines.append(f"{len(result['skipped'])} skipped")
            detail = "\n".join(f"  \u2022 {os.path.basename(s)}: {e}"
                               for s, e in result["failed"][:8])
            messagebox.showinfo("Batch Convert",
                                "\n".join(lines) + (f"\n\n{detail}" if detail else ""),
                                parent=self.root)

        return self.run_job(f"Batch {kind.replace('_', ' ')}", work, on_done=done,
                            name="batch-convert")

        row = tk.Frame(dlg, bg=BG)
        row.pack(pady=(10, 14))
        HoverButton(row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(row, "Choose folder\u2026", command=start, primary=True,
                    small=True).pack(side=tk.LEFT, padx=6)

    # ---------- statistics ----------

    def counts_ui(self):
        if not self._require_doc():
            return
        snap = self.snapshot_bytes()
        filesize = os.path.getsize(self.tool.path) if self.tool.path else 0

        def work(job):
            job.progress("Scanning\u2026", 0.3)
            return stats(snap)

        def done(info):
            box = tk.Toplevel(self.root)
            box.title("Word & Image Counts")
            box.transient(self.root)
            box.configure(bg=BG)
            rows = [
                ("Pages", f"{info['pages']}"),
                ("Words", f"{info['words']:,}"),
                ("Characters", f"{info['characters']:,}"),
                ("Embedded images", f"{info['images']:,}"),
                ("Page size", f"{info['page_size'][0]} \u00d7 {info['page_size'][1]} pt"),
                ("In-memory size", f"{info['bytes'] / 1024 / 1024:.2f} MB"),
                ("File on disk", f"{filesize / 1024 / 1024:.2f} MB" if filesize else "\u2014"),
                ("Encrypted", "yes" if info["encrypted"] else "no"),
            ]
            for label, value in rows:
                line = tk.Frame(box, bg=BG)
                line.pack(fill=tk.X, padx=18, pady=2)
                tk.Label(line, text=label, font=FONT_SMALL, bg=BG, fg=TEXT_MUTED,
                         width=18, anchor="w").pack(side=tk.LEFT)
                tk.Label(line, text=value, font=FONT_BOLD, bg=BG, fg=TEXT,
                         anchor="w").pack(side=tk.LEFT)
            HoverButton(box, "Close", command=box.destroy, small=True).pack(pady=12)
            _safe_grab(box)

        self.run_job("Document statistics", work, on_done=done, name="stats")