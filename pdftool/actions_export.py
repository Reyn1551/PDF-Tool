"""Export & optimization: image/text export, extract images, compress, page numbers."""
import os

import tkinter as tk
from tkinter import messagebox

from .theme import BG, TEXT, TEXT_MUTED, BORDER, SURFACE, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, _safe_grab
from .dialogs import native_save_file, native_save_file_any, native_open_dir
from .jobs import progress_bridge, text_bridge


class ExportActions:
    """Mixin - combined into PDFToolApp via multiple inheritance."""

    def sidebar_entries(self):
        return [("EXPORT & OPTIMIZE", [
            ("Compress", "compress_pdf"),
            ("Page Numbers", "page_numbers"),
            ("Extract Images", "extract_images_ui"),
            ("Export Text", "export_text_ui"),
        ])]


    def export_to_image(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        info = f"{n_sel} pages selected." if n_sel else f"All pages ({self.tool.page_count})."

        dlg = tk.Toplevel(self.root)
        dlg.title("Export to Image")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)

        tk.Label(dlg, text=f"Export: {info}", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(14, 4))

        fmt_row = tk.Frame(dlg, bg=BG)
        fmt_row.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(fmt_row, text="Format:", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        fmt_var = tk.StringVar(value="png")

        def on_fmt_change(*_args):
            quality_entry.config(state=tk.NORMAL if fmt_var.get() == "jpeg" else tk.DISABLED)

        for val, label in (("png", "PNG"), ("jpeg", "JPEG")):
            tk.Radiobutton(fmt_row, text=label, variable=fmt_var, value=val, command=on_fmt_change,
                           bg=BG, fg=TEXT, font=FONT, activebackground=BG,
                           selectcolor=SURFACE).pack(side=tk.LEFT, padx=(10, 0))

        q_row = tk.Frame(dlg, bg=BG)
        q_row.pack(fill=tk.X, padx=16, pady=(2, 2))
        tk.Label(q_row, text="JPEG quality (1-100):", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        quality_entry = tk.Entry(q_row, width=6, font=FONT, relief=tk.FLAT,
                                 highlightthickness=1, highlightbackground=BORDER, state=tk.DISABLED)
        quality_entry.insert(0, "90")
        quality_entry.pack(side=tk.LEFT, padx=(8, 0))

        dpi_row = tk.Frame(dlg, bg=BG)
        dpi_row.pack(fill=tk.X, padx=16, pady=(6, 2))
        tk.Label(dpi_row, text="Resolution:", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        dpi_var = tk.StringVar(value="150")
        om_dpi = tk.OptionMenu(dpi_row, dpi_var, "96", "150", "300")
        om_dpi.config(font=FONT, bg=SURFACE, relief=tk.FLAT, highlightthickness=1,
                      highlightbackground=BORDER)
        om_dpi.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(dpi_row, text="DPI (150 standard, 300 print)", font=FONT_SMALL, bg=BG,
                 fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(8, 0))

        status_lbl = tk.Label(dlg, text="", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED)
        status_lbl.pack(fill=tk.X, padx=16, pady=(2, 0))

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 14))
        btn_cancel = HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True)
        btn_cancel.pack(side=tk.LEFT, padx=6)
        btn_export = HoverButton(btn_row, "Choose Folder & Export",
                    command=lambda: self._do_export_images(dlg, fmt_var, quality_entry, dpi_var),
                    primary=True, small=True)
        btn_export.pack(side=tk.LEFT, padx=6)

    def _do_export_images(self, dlg, fmt_var, quality_entry, dpi_var):
        outdir = native_open_dir("Choose the destination folder for images")
        if not outdir:
            return None
        try:
            dpi = int(dpi_var.get())
            quality = int(quality_entry.get())
            if not (1 <= quality <= 100):
                raise ValueError
        except ValueError:
            messagebox.showerror("Export to Image", "JPEG quality must be a number between 1 and 100.", parent=self.root)
            return None
        pages = list(self.tool.selection) if self.tool.selection else list(range(self.tool.page_count))
        fmt = fmt_var.get()
        dlg.destroy()

        def work(job):
            tool = self.detached_tool()
            try:
                return tool.export_images(pages, outdir, fmt=fmt, dpi=dpi,
                                          quality=quality,
                                          progress_cb=progress_bridge(job, "Exporting"))
            finally:
                tool.close()

        def done(written):
            messagebox.showinfo("Export to Image",
                                f"{len(written)} images saved to:\n{outdir}",
                                parent=self.root)

        self.run_job("Exporting images", work, on_done=done, name="export-images")
        return None

    def compress_pdf(self):
        if not self._require_doc():
            return

        try:
            cur_size = os.path.getsize(self.tool.path)
        except Exception:
            try:
                cur_size = len(self.tool.save_to_bytes())
            except Exception:
                cur_size = 0
        cur_mb = cur_size / (1024 * 1024)

        dlg = tk.Toplevel(self.root)
        dlg.title("Compress PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)

        tk.Label(
            dlg,
            text=f"Current size: {cur_size / 1024:.0f} KB" if cur_mb < 1 else f"Current size: {cur_mb:.1f} MB",
            font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(14, 4))

        tk.Label(dlg, text="Quality:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(4, 2))
        preset = tk.StringVar(value="balanced")
        presets = [
            ("Light - almost no loss", "light"),
            ("Balanced - recommended", "balanced"),
            ("Strong - much smaller", "strong"),
            ("Extreme - smallest, still readable", "extreme"),
        ]
        for text, val in presets:
            tk.Radiobutton(dlg, text=text, variable=preset, value=val, bg=BG, fg=TEXT, font=FONT,
                           activebackground=BG, selectcolor=SURFACE, anchor="w").pack(fill=tk.X, padx=24)

        tk.Label(
            dlg,
            text="Target size in MB (optional):",
            font=FONT, bg=BG, fg=TEXT, anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(10, 2))
        target_entry = tk.Entry(dlg, width=10, font=FONT, relief=tk.FLAT,
                                highlightthickness=1, highlightbackground=BORDER)
        target_entry.pack(anchor="w", padx=16)

        status = tk.Label(dlg, text="", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w")
        status.pack(fill=tk.X, padx=16, pady=(8, 0))

        def do_compress():
            raw = target_entry.get().strip().replace(",", ".")
            target_mb = None
            if raw:
                try:
                    target_mb = float(raw)
                except ValueError:
                    messagebox.showerror("Compress PDF", "Target size must be a number, e.g. 1.5", parent=dlg)
                    return
                if target_mb <= 0:
                    messagebox.showerror("Compress PDF", "Target size must be greater than 0.", parent=dlg)
                    return

            out_path = native_save_file("Save compressed PDF", initial="compressed.pdf")
            if not out_path:
                return
            before = cur_size
            preset_name = preset.get()
            dlg.destroy()

            def work(job):
                tool = self.detached_tool()
                job.progress("Compressing\u2026", 0.25)
                try:
                    if target_mb:
                        res = tool.compress_to_size(
                            out_path, target_mb=target_mb, preset=preset_name,
                            progress=text_bridge(job))
                        return res["size_after"], res["target_reached"]
                    tool.compress(out_path, preset=preset_name)
                    return os.path.getsize(out_path), True
                finally:
                    tool.close()

            def done(payload):
                after, reached = payload
                pct = (1 - after / before) * 100 if before else 0
                msg = (f"Compression done.\n"
                       f"{self._fmt_size(before)} \u2192 {self._fmt_size(after)} "
                       f"({pct:.1f}% smaller)\n\n")
                if not reached:
                    msg += (f"Note: {target_mb:g} MB was not reachable without visible\n"
                            f"quality loss, so the best safe result "
                            f"({self._fmt_size(after)})\nwas saved instead.")
                else:
                    msg += f"Saved: {out_path}"
                messagebox.showinfo("PDF Tool", msg, parent=self.root)

            self.run_job("Compressing PDF", work, on_done=done, name="compress")

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 14))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Compress", command=do_compress, small=True).pack(side=tk.LEFT, padx=6)

    @staticmethod
    def _fmt_size(n_bytes):
        return f"{n_bytes / 1024:.0f} KB" if n_bytes < 1024 * 1024 else f"{n_bytes / (1024 * 1024):.2f} MB"

    # ---------- New feature: Page Numbers ----------
    def page_numbers(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Page Numbers")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Start from number:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 2))
        start = tk.Entry(dlg, width=8, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        start.insert(0, "1")
        start.pack(anchor="w", padx=16)
        tk.Label(dlg, text="Position:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(8, 2))
        pos = tk.StringVar(value="bottom")
        for text, val in [("Bottom center", "bottom"), ("Top center", "top"),
                          ("Bottom right", "bottom-right"), ("Bottom left", "bottom-left")]:
            tk.Radiobutton(dlg, text=text, variable=pos, value=val, bg=BG, fg=TEXT, font=FONT,
                           activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Apply", command=lambda: self._do_page_numbers(dlg, start, pos),
                    primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_page_numbers(self, dlg, start_entry, pos_var):
        try:
            start = int(start_entry.get())
        except ValueError:
            messagebox.showerror("PDF Tool", "Start number must be an integer.", parent=dlg)
            return
        position = pos_var.get()
        dlg.destroy()

        def mutate(tool, job):
            job.progress("Numbering pages\u2026", 0.4)
            tool.add_page_numbers(start=start, position=position)

        self.mutate_in_job("Page numbers", mutate, label="Page numbers",
                           name="page-numbers")

    def export_text_ui(self):
        if not self._require_doc():
            return
        pages = list(self.tool.selection) if self.tool.selection else None
        base = os.path.splitext(os.path.basename(self.tool.path))[0] if self.tool.path else "document"
        out = native_save_file_any("Save text (.txt)", initial=base + ".txt")
        if not out:
            return
        pages_list = pages

        def work(job):
            job.progress("Writing text\u2026", 0.5)
            tool = self.detached_tool()
            try:
                tool.export_text(out, pages=pages_list)
            finally:
                tool.close()

        self.run_job(
            "PDF \u2192 Text", work,
            on_done=lambda _r: messagebox.showinfo(
                "PDF \u2192 Text", f"Text saved:\n{out}", parent=self.root),
            name="export-text")

    # ---------- New feature: Extract images ----------
    def extract_images_ui(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        pages = list(self.tool.selection) if n_sel else list(range(self.tool.page_count))
        outdir = native_open_dir("Choose the destination folder for images")
        if not outdir:
            return
        def work(job):
            tool = self.detached_tool()
            try:
                return tool.extract_images(pages, outdir,
                                           progress_cb=progress_bridge(job, "Extracting"))
            finally:
                tool.close()

        def done(written):
            if written:
                messagebox.showinfo("Extract Images",
                                    f"{len(written)} images saved to:\n{outdir}",
                                    parent=self.root)
            else:
                messagebox.showinfo("Extract Images", "No embedded images found.",
                                    parent=self.root)

        self.run_job("Extracting images", work, on_done=done, name="extract-images")
