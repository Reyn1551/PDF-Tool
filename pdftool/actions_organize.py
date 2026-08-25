"""Aksi organisasi halaman: buka, hapus, merge, split, rotasi, urut, crop."""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

import fitz

from .theme import BG, TEXT, TEXT_MUTED, ACCENT, BORDER, SURFACE, DANGER, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, SidebarItem, _safe_grab, _set_app_icon
from .dialogs import (
    native_open_file, native_open_files, native_save_file, native_open_dir,
)
from pdf_ops import PDFTool


class OrganizeActions:
    """Mixin — digabungkan ke PDFToolApp lewat pewarisan berganda."""

    def open_pdf(self):
        path = native_open_file("Pilih PDF")
        if not path:
            return
        password = None
        for attempt in range(3):  # maksimal 3 percobaan password
            try:
                self.tool.open(path, password)
                break
            except ValueError as e:
                if str(e) == "NEEDS_PASSWORD":
                    password = simpledialog.askstring(
                        "Password",
                        "PDF ini dilindungi password.\nMasukkan password"
                        + (f"  (percobaan {attempt + 1}/3)" if attempt else "")
                        + ":",
                        show="*", parent=self.root,
                    )
                    if password is None:
                        return  # user membatalkan
                else:
                    messagebox.showerror("PDF Tool", str(e), parent=self.root)
                    return
        else:
            messagebox.showerror("PDF Tool", "Password salah 3 kali.", parent=self.root)
            return
        self._refresh_preview()

    def select_all(self):
        if not self._require_doc():
            return
        self.tool.select_all()
        self._refresh_preview()

    def delete_pages(self):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Pilih halaman yang mau dihapus dulu.", parent=self.root)
            return
        if not messagebox.askyesno("Hapus halaman", f"Hapus {len(self.tool.selection)} halaman terpilih?", parent=self.root):
            return
        try:
            self.tool.delete_pages(self.tool.selection)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def merge_pdf(self):
        if not self._require_doc():
            return
        paths = native_open_files("Pilih PDF untuk digabung")
        if not paths:
            return
        try:
            self.tool.merge(list(paths))
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal merge: {e}", parent=self.root)
            return
        self._refresh_preview()

    def insert_pages(self):
        if not self._require_doc():
            return
        path = native_open_file("Pilih PDF sumber")
        if not path:
            return
        try:
            other = PDFTool()
            other.open(path)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        total = other.page_count
        other.close()
        sel_src = simpledialog.askstring(
            "Sisip dari PDF lain",
            f"PDF sumber punya {total} halaman.\nNomor halaman yang mau disisipkan (pisahkan dengan koma, contoh: 1,3,5-7):",
            parent=self.root,
        )
        if not sel_src:
            return
        try:
            idx = self._parse_range(sel_src, total)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        pos = simpledialog.askinteger(
            "Sisip dari PDF lain",
            f"Posisi sisip di PDF utama (1-{self.tool.page_count + 1}):",
            initialvalue=self.tool.page_count + 1, minvalue=1, maxvalue=self.tool.page_count + 1,
            parent=self.root,
        )
        if pos is None:
            return
        try:
            self.tool.insert_from(path, idx, pos - 1)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def split_pdf(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Split PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        var = tk.StringVar(value="range")
        tk.Radiobutton(dlg, text="Rentang halaman (contoh: 3-7 atau 1,3,5-8)", variable=var, value="range",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Radiobutton(dlg, text="Semua halaman (tiap halaman jadi file terpisah)", variable=var, value="all",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=16, pady=2)
        entry = tk.Entry(dlg, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry.pack(padx=16, pady=(8, 12), fill=tk.X)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(0, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Split", command=lambda: self._do_split(dlg, var.get(), entry.get()), primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_split(self, dlg, mode, rng):
        dlg.destroy()
        try:
            if mode == "all":
                outdir = native_open_dir("Folder tujuan simpan")
                if not outdir:
                    return
                for i in range(self.tool.page_count):
                    single = fitz.open()
                    single.insert_pdf(self.tool.doc, from_page=i, to_page=i)
                    single.save(os.path.join(outdir, f"halaman_{i+1}.pdf"))
                    single.close()
            else:
                if not rng:
                    raise ValueError("Isi rentang halaman dulu (contoh: 3-7 atau 1,3,5-8).")
                idx = self._parse_range(rng, self.tool.page_count)
                path = native_save_file("Simpan hasil split", initial="split.pdf")
                if not path:
                    return
                out = fitz.open()
                for i in idx:
                    out.insert_pdf(self.tool.doc, from_page=i, to_page=i)
                out.save(path)
                out.close()
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Split gagal: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF Tool", "Split selesai.", parent=self.root)

    def rotate(self, angle):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Pilih halaman yang mau diputar dulu.", parent=self.root)
            return
        try:
            self.tool.rotate(self.tool.selection, angle)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def move_pages(self):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Pilih halaman yang mau dipindah dulu.", parent=self.root)
            return
        n = self.tool.page_count - len(self.tool.selection)
        target = simpledialog.askinteger(
            "Urut Ulang",
            f"Posisi tujuan (1-{n + 1}). Contoh: pindahkan halaman 3,5 ke posisi 1:",
            initialvalue=1, minvalue=1, maxvalue=n + 1, parent=self.root,
        )
        if target is None:
            return
        try:
            self.tool.move_pages(self.tool.selection, target - 1)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def save_as(self):
        if not self._require_doc():
            return
        path = native_save_file("Simpan PDF")
        if not path:
            return
        try:
            self.tool.save_as(path)
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal simpan: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF Tool", f"Tersimpan: {path}", parent=self.root)

    def reverse_pages(self):
        if not self._require_doc():
            return
        try:
            self.tool.reverse_pages()
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal membalik urutan: {e}", parent=self.root)
            return
        self._refresh_preview()

    # ---------- Fitur baru: Crop ----------
    def crop_pages_ui(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        info = f"{n_sel} halaman terpilih." if n_sel else "Semua halaman."
        dlg = tk.Toplevel(self.root)
        dlg.title("Crop / Potong Tepi")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text=f"Potong tepi: {info}", font=FONT_BOLD, bg=BG, fg=TEXT).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(dlg, text="Rasio potong tiap sisi (0 - 0.45 dari ukuran halaman):",
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16)
        row = tk.Frame(dlg, bg=BG)
        row.pack(padx=16, pady=8)
        vars_ = {}
        for i, (label, default) in enumerate((("Kiri", "0"), ("Atas", "0"), ("Kanan", "0"), ("Bawah", "0"))):
            tk.Label(row, text=label, font=FONT_SMALL, bg=BG, fg=TEXT).grid(row=0, column=i, padx=(0, 4))
            e = tk.Entry(row, width=6, font=FONT_SMALL, relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=BORDER, justify=tk.CENTER)
            e.insert(0, default)
            e.grid(row=1, column=i, padx=(0, 10))
            vars_[label] = e
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(6, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)

        def do_crop():
            try:
                vals = [float(vars_[k].get() or 0) for k in ("Kiri", "Atas", "Kanan", "Bawah")]
            except ValueError:
                messagebox.showerror("Crop", "Semua nilai harus angka.", parent=dlg)
                return
            pages = list(self.tool.selection) if self.tool.selection else None
            dlg.destroy()
            try:
                self.tool.crop_pages(pages, left=vals[0], top=vals[1], right=vals[2], bottom=vals[3])
            except ValueError as e:
                messagebox.showerror("Crop", str(e), parent=self.root)
                return
            self._refresh_preview()

        HoverButton(btn_row, "Potong", command=do_crop, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _parse_range(self, s, total):
        idx = []
        for part in s.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-")
                a, b = int(a), int(b)
                if a < 1 or b > total or a > b:
                    raise ValueError(f"Rentang {part} tidak valid (maks {total}).")
                idx.extend(range(a - 1, b))
            else:
                v = int(part)
                if v < 1 or v > total:
                    raise ValueError(f"Halaman {v} tidak valid (maks {total}).")
                idx.append(v - 1)
        if not idx:
            raise ValueError("Tidak ada halaman dipilih.")
        return idx
