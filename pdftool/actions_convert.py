"""Cross-format conversion: image/office/html to PDF and PDF to Word."""
import os

import tkinter as tk
from tkinter import messagebox

from .theme import BG, TEXT, BORDER, SURFACE, FONT, FONT_BOLD
from .widgets import HoverButton, _safe_grab
from .dialogs import (native_open_files_images, native_open_file_office, native_open_file, native_save_file_any, native_open_dir)
from pdf_convert import (
    images_to_pdf, office_to_pdf, pdf_to_docx, html_to_pdf, find_soffice,
)


class ConvertActions:
    """Mixin — combined into PDFToolApp via multiple inheritance."""

    def sidebar_entries(self):
        return [("CONVERT", [
            ("PDF \u2192 Image (JPG/PNG)", "export_to_image"),
            ("Image \u2192 PDF", "images_to_pdf_ui"),
            ("Office \u2192 PDF", "office_to_pdf_ui"),
            ("PDF \u2192 Word (.docx)", "pdf_to_word_ui"),
            ("HTML \u2192 PDF", "html_to_pdf_ui"),
        ])]

    def standalone_methods(self):
        return {"images_to_pdf_ui", "office_to_pdf_ui", "html_to_pdf_ui"}


    def standalone_methods(self):
        return {"images_to_pdf_ui", "office_to_pdf_ui", "html_to_pdf_ui",
                "repair_pdf_ui"}

    def images_to_pdf_ui(self):
        paths = native_open_files_images("Select images (multiple)")
        if not paths:
            return
        out = native_save_file_any("Save PDF from images", initial="images.pdf")
        if not out:
            return
        mode_var = tk.StringVar(value="a4")
        dlg = tk.Toplevel(self.root)
        dlg.title("Image \u2192 PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text=f"{len(paths)} images selected.", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg, text="Page size:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(anchor="w", padx=16)
        tk.Radiobutton(dlg, text="A4 (portrait, auto-fit images)", variable=mode_var, value="a4",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)
        tk.Radiobutton(dlg, text="Follow image size", variable=mode_var, value="auto",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)

        def do_convert():
            mode = mode_var.get()
            dlg.destroy()

            def work(job):
                job.progress("Converting images\u2026")
                images_to_pdf(paths, out, page_mode=mode)
                return out

            def done(_out):
                messagebox.showinfo(
                    "Image \u2192 PDF",
                    f"Done!\n{len(paths)} images converted to PDF:\n{out}",
                    parent=self.root)

            self.run_job("Image \u2192 PDF", work, on_done=done, name="images-to-pdf")

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(10, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Create PDF", command=do_convert, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- New feature: Office -> PDF ----------
    def office_to_pdf_ui(self):
        path = native_open_file_office("Select Word/Excel/PPT document")
        if not path:
            return
        if not find_soffice():
            messagebox.showerror(
                "Office \u2192 PDF",
                "LibreOffice not found on this system.\n\n"
                "Install with:\n  sudo apt install libreoffice",
                parent=self.root,
            )
            return
        outdir = native_open_dir("Destination folder for converted PDF")
        if not outdir:
            return
        def work(job):
            job.progress(f"Converting {os.path.basename(path)}\u2026")
            return office_to_pdf(path, outdir)

        def done(result):
            messagebox.showinfo("Office \u2192 PDF", f"Conversion complete:\n{result}",
                                parent=self.root)

        self.run_job("Office \u2192 PDF", work, on_done=done, name="office-to-pdf")

    # ---------- New feature: PDF -> Word ----------
    def pdf_to_word_ui(self):
        path = native_open_file("Select source PDF") if not (self.tool.path and self.tool.doc) else None
        src = path or self.tool.path
        if not src:
            messagebox.showinfo("PDF \u2192 Word", "Open or select a PDF first.", parent=self.root)
            return
        initial = os.path.splitext(os.path.basename(src))[0] + ".docx"
        out = native_save_file_any("Save Word document", initial=initial)
        if not out:
            return
        def work(job):
            job.progress("Converting to Word\u2026")
            return pdf_to_docx(src, out)

        def done(n):
            messagebox.showinfo(
                "PDF \u2192 Word",
                f"Done ({n} pages).\nNote: best-effort result as text per paragraph,\n"
                f"complex layouts may differ.\n\nSaved: {out}",
                parent=self.root)

        self.run_job("PDF \u2192 Word", work, on_done=done, name="pdf-to-word")

    # ---------- New feature: HTML -> PDF ----------
    def html_to_pdf_ui(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("HTML \u2192 PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        dlg.geometry("520x420")
        tk.Label(dlg, text="Paste HTML code:", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 4))
        txt = tk.Text(dlg, font=("DejaVu Sans Mono", 9), relief=tk.FLAT,
                      highlightthickness=1, highlightbackground=BORDER)
        txt.insert("1.0", "<h1>Title</h1>\n<p>Write <b>HTML</b> here.</p>")
        txt.pack(fill=tk.BOTH, expand=True, padx=16)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=10)

        def do_convert():
            html = txt.get("1.0", tk.END)
            if not html.strip():
                messagebox.showinfo("HTML \u2192 PDF", "HTML is still empty.", parent=dlg)
                return
            out = native_save_file_any("Save PDF from HTML", initial="document.pdf")
            if not out:
                return
            dlg.destroy()

            def work(job):
                job.progress("Rendering HTML\u2026")
                html_to_pdf(html, out)
                return out

            def done(_out):
                messagebox.showinfo("HTML \u2192 PDF", f"Done:\n{out}", parent=self.root)

            self.run_job("HTML \u2192 PDF", work, on_done=done, name="html-to-pdf")

        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Create PDF", command=do_convert, primary=True, small=True).pack(side=tk.LEFT, padx=6)
