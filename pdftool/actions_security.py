"""Aksi keamanan & dekorasi: watermark, proteksi password, unlock, repair."""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

import fitz

from .theme import BG, TEXT, TEXT_MUTED, ACCENT, BORDER, SURFACE, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, _safe_grab
from .dialogs import native_open_file, native_save_file


class SecurityActions:
    """Mixin — digabungkan ke PDFToolApp lewat pewarisan berganda."""

    def watermark(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Watermark")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Teks watermark (kosongkan jika pakai gambar)", font=FONT, bg=BG, fg=TEXT,
                 anchor="w").grid(row=0, column=0, columnspan=2, padx=16, pady=(12, 4), sticky="w")
        txt = tk.Entry(dlg, width=36, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        txt.grid(row=1, column=0, columnspan=2, padx=16, pady=2, sticky="we")
        tk.Label(dlg, text="Gambar PNG:", font=FONT, bg=BG, fg=TEXT, anchor="w").grid(row=2, column=0, padx=16, pady=(8, 4), sticky="w")
        img_path = tk.StringVar()
        img_entry = tk.Entry(dlg, textvariable=img_path, width=24, font=FONT, relief=tk.FLAT,
                             highlightthickness=1, highlightbackground=BORDER)
        img_entry.grid(row=3, column=0, padx=(16, 4), pady=2, sticky="we")
        HoverButton(dlg, "\u2026", command=lambda: img_path.set(native_open_file("Pilih gambar", pdf_only=False)), small=True).grid(row=3, column=1, padx=(0, 16))
        tk.Label(dlg, text="Opacity (0-1, contoh 0.3):", font=FONT, bg=BG, fg=TEXT, anchor="w").grid(row=4, column=0, padx=16, pady=(8, 4), sticky="w")
        opacity = tk.Entry(dlg, width=8, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        opacity.insert(0, "0.3")
        opacity.grid(row=5, column=0, padx=16, pady=2, sticky="w")
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.grid(row=6, column=0, columnspan=2, pady=(12, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Terapkan", command=lambda: self._do_watermark(dlg, txt.get(), img_path.get(), opacity.get()), primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_watermark(self, dlg, text, img, op):
        try:
            opacity = float(op)
        except ValueError:
            messagebox.showerror("PDF Tool", "Opacity harus angka.", parent=self.root)
            return
        try:
            self.tool.add_watermark(text if text else None, img if img else None, opacity)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        dlg.destroy()
        self._refresh_preview()

    def password(self):
        if not self._require_doc():
            return
        pw = simpledialog.askstring("Password", "Password untuk PDF (kosongkan = batal):", show="*", parent=self.root)
        if not pw:
            return
        path = native_save_file("Simpan PDF ber-password", initial="protected.pdf")
        if not path:
            return
        try:
            # Enkripsi dari kondisi dokumen TERKINI (termasuk editan yang belum disimpan),
            # bukan file lama di disk seperti implementasi sebelumnya.
            data = self.tool.save_to_bytes()
            doc = fitz.open(stream=data, filetype="pdf")
            doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=pw, user_pw=pw)
            doc.close()
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF Tool", f"PDF tersimpan: {path}", parent=self.root)

    def unlock_pdf(self):
        if self.tool.doc and self.tool.was_encrypted:
            # dokumen aktif sudah terbuka dengan password -> langsung simpan salinan bebas
            try:
                target = self.tool.unlock()
            except Exception as e:
                messagebox.showerror("Unlock", str(e), parent=self.root)
                return
            messagebox.showinfo("Unlock", f"Salinan tanpa password tersimpan:\n{target}", parent=self.root)
            return
        # Pilih file terenkripsi lain
        path = native_open_file("Pilih PDF terenkripsi")
        if not path:
            return
        pw = simpledialog.askstring("Unlock", "Password PDF tersebut:", show="*", parent=self.root)
        if pw is None:
            return
        tmp = PDFTool()
        try:
            tmp.open(path, pw)
        except ValueError as e:
            messagebox.showerror("Unlock", str(e), parent=self.root)
            return
        try:
            target = os.path.splitext(path)[0] + "_unlocked.pdf"
            data = tmp.save_to_bytes()
            doc = fitz.open(stream=data, filetype="pdf")
            doc.save(target, encryption=fitz.PDF_ENCRYPT_NONE, permissions=int(-1))
            doc.close()
        except Exception as e:
            messagebox.showerror("Unlock", f"Gagal: {e}", parent=self.root)
            return
        finally:
            tmp.close()
        messagebox.showinfo("Unlock", f"Salinan tanpa password tersimpan:\n{target}", parent=self.root)

    # ---------- Fitur baru: Repair ----------
    def repair_pdf_ui(self):
        path = native_open_file("Pilih PDF yang rusak")
        if not path:
            return
        out = native_save_file("Simpan hasil perbaikan", initial=os.path.splitext(os.path.basename(path))[0] + "_fixed.pdf")
        if not out:
            return
        ok, msg = repair_pdf(path, out)
        if ok:
            messagebox.showinfo("Repair", msg, parent=self.root)
        else:
            messagebox.showerror("Repair", msg, parent=self.root)
