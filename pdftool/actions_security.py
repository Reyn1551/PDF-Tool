"""Security & decoration actions: watermark, password protection, unlock, repair."""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

import fitz

from .theme import BG, TEXT, BORDER, FONT
from .widgets import HoverButton, _safe_grab
from .dialogs import native_open_file, native_save_file
from pdf_ops import PDFTool, repair_pdf


class SecurityActions:
    """Mixin — combined into PDFToolApp via multiple inheritance."""

    def sidebar_entries(self):
        return [("SECURITY", [
            ("Watermark", "watermark"),
            ("Protect with Password", "password"),
            ("Unlock Password", "unlock_pdf"),
            ("Repair Broken PDF", "repair_pdf_ui"),
        ])]

    def standalone_methods(self):
        return {"repair_pdf_ui"}


    def watermark(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Watermark")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Watermark text (leave empty to use image)", font=FONT, bg=BG, fg=TEXT,
                 anchor="w").grid(row=0, column=0, columnspan=2, padx=16, pady=(12, 4), sticky="w")
        txt = tk.Entry(dlg, width=36, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        txt.grid(row=1, column=0, columnspan=2, padx=16, pady=2, sticky="we")
        tk.Label(dlg, text="PNG image:", font=FONT, bg=BG, fg=TEXT, anchor="w").grid(row=2, column=0, padx=16, pady=(8, 4), sticky="w")
        img_path = tk.StringVar()
        img_entry = tk.Entry(dlg, textvariable=img_path, width=24, font=FONT, relief=tk.FLAT,
                             highlightthickness=1, highlightbackground=BORDER)
        img_entry.grid(row=3, column=0, padx=(16, 4), pady=2, sticky="we")
        HoverButton(dlg, "\u2026", command=lambda: img_path.set(native_open_file("Select image", pdf_only=False)), small=True).grid(row=3, column=1, padx=(0, 16))
        tk.Label(dlg, text="Opacity (0-1, e.g. 0.3):", font=FONT, bg=BG, fg=TEXT, anchor="w").grid(row=4, column=0, padx=16, pady=(8, 4), sticky="w")
        opacity = tk.Entry(dlg, width=8, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        opacity.insert(0, "0.3")
        opacity.grid(row=5, column=0, padx=16, pady=2, sticky="w")
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.grid(row=6, column=0, columnspan=2, pady=(12, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Apply", command=lambda: self._do_watermark(dlg, txt.get(), img_path.get(), opacity.get()), primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_watermark(self, dlg, text, img, op):
        try:
            opacity = float(op)
        except ValueError:
            messagebox.showerror("PDF Tool", "Opacity must be a number.", parent=self.root)
            return
        if not text and not img:
            messagebox.showinfo("Watermark", "Type a watermark text or pick an image.",
                                parent=dlg)
            return
        if img and not os.path.isfile(img):
            messagebox.showerror("Watermark", f"Image not found:\n{img}", parent=dlg)
            return
        dlg.destroy()
        source_text, source_img = (text or None), (img or None)

        def mutate(tool, job):
            job.progress("Applying watermark\u2026", 0.3)
            tool.add_watermark(source_text, source_img, opacity)

        self.mutate_in_job("Watermark", mutate, label="Watermark", name="watermark")

    def password(self):
        if not self._require_doc():
            return
        pw = simpledialog.askstring("Password", "Password for PDF (leave empty = cancel):", show="*", parent=self.root)
        if not pw:
            return
        path = native_save_file("Save password-protected PDF", initial="protected.pdf")
        if not path:
            return
        # Encrypt from the CURRENT document state (including unsaved edits),
        # not the old file on disk as in the previous implementation.
        data = self.tool.save_to_bytes()

        def work(job):
            job.progress("Encrypting\u2026", 0.4)
            doc = fitz.open(stream=data, filetype="pdf")
            try:
                doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
                         owner_pw=pw, user_pw=pw)
            finally:
                doc.close()
            return path

        def done(_p):
            messagebox.showinfo("PDF Tool", f"PDF saved: {path}", parent=self.root)

        self.run_job("Protecting PDF", work, on_done=done, name="protect")

    def unlock_pdf(self):
        if self.tool.doc and self.tool.was_encrypted:
            # active document already opened with password -> save a password-free copy directly
            try:
                target = self.tool.unlock()
            except Exception as e:
                messagebox.showerror("Unlock", str(e), parent=self.root)
                return
            messagebox.showinfo("Unlock", f"Password-free copy saved:\n{target}", parent=self.root)
            return
        # Select another encrypted file
        path = native_open_file("Select encrypted PDF")
        if not path:
            return
        pw = simpledialog.askstring("Unlock", "Password for that PDF:", show="*", parent=self.root)
        if pw is None:
            return
        target = os.path.splitext(path)[0] + "_unlocked.pdf"

        def work(job):
            job.progress("Removing protection\u2026")
            tmp = PDFTool()
            try:
                tmp.open(path, pw)
                data = tmp.save_to_bytes()
            finally:
                tmp.close()
            doc = fitz.open(stream=data, filetype="pdf")
            try:
                doc.save(target, encryption=fitz.PDF_ENCRYPT_NONE, permissions=int(-1))
            finally:
                doc.close()
            return target

        def done(out):
            messagebox.showinfo("Unlock", f"Password-free copy saved:\n{out}",
                                parent=self.root)

        self.run_job("Unlocking PDF", work, on_done=done, name="unlock")

    # ---------- New feature: Repair ----------
    def repair_pdf_ui(self):
        path = native_open_file("Select broken PDF")
        if not path:
            return
        out = native_save_file("Save repair result",
                              initial=os.path.splitext(os.path.basename(path))[0] + "_fixed.pdf")
        if not out:
            return

        def work(job):
            job.progress("Rebuilding document\u2026")
            return repair_pdf(path, out)

        def done(result):
            ok, msg = result
            if ok:
                messagebox.showinfo("Repair", msg, parent=self.root)
            else:
                messagebox.showerror("Repair", msg, parent=self.root)

        self.run_job("Repairing PDF", work, on_done=done, name="repair")
