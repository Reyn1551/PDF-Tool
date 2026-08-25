"""Export & optimization: image/text export, extract images, compress, page numbers."""
import os

import tkinter as tk
from tkinter import messagebox

import fitz

from .theme import BG, TEXT, TEXT_MUTED, ACCENT, BORDER, SURFACE, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, _safe_grab
from .dialogs import native_save_file, native_save_file_any, native_open_dir


class ExportActions:
    """Mixin - combined into PDFToolApp via multiple inheritance."""

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

        try:
            dlg.config(cursor="watch")
        except Exception:
            pass
        self.root.config(cursor="watch")
        try:
            written = self.tool.export_images(
                pages, outdir, fmt=fmt, dpi=dpi, quality=quality
            )
        except Exception as e:
            messagebox.showerror("Export to Image", f"Export failed: {e}", parent=self.root)
            return None
        finally:
            try:
                if dlg.winfo_exists():
                    dlg.config(cursor="")
                    dlg.destroy()
            except Exception:
                pass
            try:
                self.root.config(cursor="")
            except Exception:
                pass

        messagebox.showinfo("Export to Image", f"{len(written)} images saved to:\n{outdir}", parent=self.root)
        return written

    def compress_pdf(self):
        if not self._require_doc():
            return
        path = native_save_file("Save compressed PDF", initial="compressed.pdf")
        if not path:
            return
        try:
            before = os.path.getsize(self.tool.path)
            self.tool.compress(path)
            after = os.path.getsize(path)
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Compression failed: {e}", parent=self.root)
            return
        pct = (1 - after / before) * 100 if before else 0
        messagebox.showinfo(
            "PDF Tool",
            f"Compression done.\n{before / 1024:.1f} KB \u2192 {after / 1024:.1f} KB "
            f"({pct:.1f}% smaller)\n\nSaved: {path}",
            parent=self.root,
        )

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
        try:
            self.tool.add_page_numbers(start=start, position=pos_var.get())
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Failed: {e}", parent=dlg)
            return
        dlg.destroy()
        self._refresh_preview()

    def export_text_ui(self):
        if not self._require_doc():
            return
        pages = list(self.tool.selection) if self.tool.selection else None
        base = os.path.splitext(os.path.basename(self.tool.path))[0] if self.tool.path else "document"
        out = native_save_file_any("Save text (.txt)", initial=base + ".txt")
        if not out:
            return
        try:
            self.tool.export_text(out, pages=pages)
        except Exception as e:
            messagebox.showerror("PDF \u2192 Text", f"Failed: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF \u2192 Text", f"Text saved:\n{out}", parent=self.root)

    # ---------- New feature: Extract images ----------
    def extract_images_ui(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        pages = list(self.tool.selection) if n_sel else list(range(self.tool.page_count))
        outdir = native_open_dir("Choose the destination folder for images")
        if not outdir:
            return
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            written = self.tool.extract_images(pages, outdir)
        except ValueError as e:
            messagebox.showerror("Extract Images", str(e), parent=self.root)
            return
        finally:
            self.root.config(cursor="")
        if written:
            messagebox.showinfo("Extract Images", f"{len(written)} images saved to:\n{outdir}", parent=self.root)
        else:
            messagebox.showinfo("Extract Images", "No embedded images found.", parent=self.root)
