"""Inti aplikasi PDF Tool: layout, preview thumbnail, status, routing scroll.

Logika aksi (menu sidebar) ada di modul actions_* sebagai mixin; modul ini
hanya kerangka UI supaya mudah dinavigasi.
"""
import os

import tkinter as tk
from tkinter import messagebox

import fitz

from .theme import (
    BG, SURFACE, SIDEBAR, BORDER, TEXT, TEXT_MUTED, ACCENT, ACCENT_HOV,
    ACCENT_SOFT, HOVER, DANGER, FONT, FONT_SMALL, FONT_BOLD, FONT_TITLE,
)
from .widgets import HoverButton, SidebarItem, _set_app_icon
from .actions_organize import OrganizeActions
from .actions_security import SecurityActions
from .actions_export import ExportActions
from .actions_edit import EditActions
from .actions_convert import ConvertActions
from pdf_ops import PDFTool


class PDFToolApp(
    OrganizeActions,
    SecurityActions,
    ExportActions,
    EditActions,
    ConvertActions,
):
    def __init__(self, root):
        self.root = root
        self.tool = PDFTool()
        root.title("PDF Tool")
        root.geometry("1080x720")
        root.minsize(900, 560)
        root.configure(bg=BG)
        _set_app_icon(root)

        self._build_header()
        self._build_body()
        self._build_status()
        self._sync_enabled()

    # ---------- Header ----------
    def _build_header(self):
        head = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        head.pack(side=tk.TOP, fill=tk.X)

        dot = tk.Canvas(head, width=16, height=16, bg=SURFACE, highlightthickness=0)
        dot.create_oval(2, 2, 14, 14, fill=ACCENT, outline="")
        dot.pack(side=tk.LEFT, padx=(16, 8), pady=12)
        tk.Label(head, text="PDF Tool", font=FONT_TITLE, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)

        self.file_label = tk.Label(head, text="Belum ada file", font=FONT_SMALL, bg=SURFACE,
                                   fg=TEXT_MUTED)
        self.file_label.pack(side=tk.LEFT, padx=16)

        self.btn_select_all = HoverButton(head, "Pilih Semua", command=self.select_all, small=True)
        self.btn_select_all.pack(side=tk.RIGHT, padx=(4, 8), pady=8)
        self.btn_save = HoverButton(head, "Simpan", command=self.save_as, primary=True, small=True)
        self.btn_save.pack(side=tk.RIGHT, padx=(4, 16), pady=8)

    # ---------- Body ----------
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(body)
        self._build_preview(body)
        # Routing scroll cerdas: area mana pun di bawah kursor yang discroll.
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
        target.yview_scroll(step, "units")

    def _build_sidebar(self, parent):
        outer = tk.Frame(parent, bg=SIDEBAR, width=230, highlightthickness=1, highlightbackground=BORDER)
        outer.pack(side=tk.LEFT, fill=tk.Y)
        outer.pack_propagate(False)

        self.sidebar_canvas = tk.Canvas(outer, bg=SIDEBAR, highlightthickness=0, width=230)
        sbs = tk.Scrollbar(outer, orient=tk.VERTICAL, command=self.sidebar_canvas.yview)
        self.sidebar_canvas.configure(yscrollcommand=sbs.set)
        sbs.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(self.sidebar_canvas, bg=SIDEBAR)
        self.sidebar_canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")
        inner.bind("<Configure>", lambda e: self.sidebar_canvas.configure(
            scrollregion=self.sidebar_canvas.bbox("all")))
        self.sidebar_canvas.bind("<Configure>", lambda e: self.sidebar_canvas.itemconfig(
            "inner", width=e.width))

        sb = inner
        self._sidebar_group(sb, "FILE", [("Buka PDF", self.open_pdf, False)])
        self._sidebar_group(sb, "ORGANISASI HALAMAN", [
            ("Hapus Halaman", self.delete_pages, False),
            ("Sisip dari PDF lain", self.insert_pages, False),
            ("Merge PDF", self.merge_pdf, False),
            ("Split PDF", self.split_pdf, False),
            ("Rotasi 90\u00b0", lambda: self.rotate(90), False),
            ("Urut Ulang", self.move_pages, False),
            ("Balik Urutan", self.reverse_pages, False),
            ("Crop / Potong Tepi", self.crop_pages_ui, False),
        ])
        self._sidebar_group(sb, "KONVERSI", [
            ("PDF \u2192 Image (JPG/PNG)", self.export_to_image, False),
            ("Image \u2192 PDF", self.images_to_pdf_ui, False),
            ("Office \u2192 PDF", self.office_to_pdf_ui, False),
            ("PDF \u2192 Word (.docx)", self.pdf_to_word_ui, False),
            ("HTML \u2192 PDF", self.html_to_pdf_ui, False),
            ("PDF \u2192 Text", self.export_text_ui, False),
        ])
        self._sidebar_group(sb, "EDIT & ANOTASI", [
            ("Edit Teks", self.edit_text, False),
            ("Stempel / Tanda Tangan", self.stamp_image_ui, False),
            ("Info Dokumen (Metadata)", self.metadata_ui, False),
        ])
        self._sidebar_group(sb, "KEAMANAN & OPTIMASI", [
            ("Watermark", self.watermark, False),
            ("Password (Proteksi)", self.password, False),
            ("Buka Password (Unlock)", self.unlock_pdf, False),
            ("Repair PDF Rusak", self.repair_pdf_ui, False),
            ("Compress", self.compress_pdf, False),
            ("Nomor Halaman", self.page_numbers, False),
            ("Ekstrak Gambar", self.extract_images_ui, False),
        ])
        self.sidebar_items = []
        # Fitur konversi/repair tidak butuh PDF terbuka -> tetap aktif.
        standalone = {"images_to_pdf_ui", "office_to_pdf_ui", "html_to_pdf_ui", "repair_pdf_ui"}
        for w in sb.winfo_children():
            if isinstance(w, SidebarItem):
                self.sidebar_items.append(w)
                w.needs_doc = getattr(w.command, "__name__", "") not in standalone

    def _sidebar_group(self, parent, title, items):
        tk.Label(parent, text=title, font=FONT_SMALL, bg=SIDEBAR, fg=TEXT_MUTED,
                 anchor="w", padx=14, pady=6).pack(fill=tk.X)
        for text, cmd, accent in items:
            item = SidebarItem(parent, text, cmd, accent)
            item.pack(fill=tk.X, padx=6, pady=1)

    def _build_preview(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview, bg=BORDER,
                          activebackground="#c8ccd2", troughcolor=BG, relief=tk.FLAT)
        self.canvas.configure(yscrollcommand=sb.set)
        self.frame = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.window, width=e.width))
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ---------- Status ----------
    def _build_status(self):
        self.status = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = tk.Label(self.status, text="Buka PDF untuk mulai", font=FONT_SMALL,
                                     bg=SURFACE, fg=TEXT_MUTED, anchor="w", padx=16, pady=6)
        self.status_label.pack(fill=tk.X)

    # ---------- Helpers ----------
    def _sync_enabled(self):
        has = self.tool.doc is not None
        self.btn_save.set_enabled(has)
        self.btn_select_all.set_enabled(has)
        for item in self.sidebar_items:
            item.set_enabled(has or not getattr(item, "needs_doc", True))
        self.sidebar_items[0].set_enabled(True)

    def _refresh_status(self):
        if not self.tool.doc:
            self.file_label.config(text="Belum ada file")
            self.status_label.config(text="Buka PDF untuk mulai")
            return
        name = os.path.basename(self.tool.path)
        sel = self.tool.selection
        self.file_label.config(text=f"{name}  \u00b7  {self.tool.page_count} halaman")
        if sel:
            shown = ", ".join(str(i + 1) for i in sel[:15])
            more = f" \u2026 (+{len(sel) - 15})" if len(sel) > 15 else ""
            self.status_label.config(text=f"Terpilih {len(sel)} halaman:  {shown}{more}", fg=ACCENT)
        else:
            self.status_label.config(text="Klik halaman untuk memilih", fg=TEXT_MUTED)

    def _refresh_preview(self):
        for w in self.frame.winfo_children():
            w.destroy()
        if not self.tool.doc:
            self._sync_enabled()
            return
        cols = 4
        for i in range(self.tool.page_count):
            try:
                thumb = self.tool.get_thumb(i)
                # PyMuPDF docs: PPM mentah didukung PhotoImage(data=...) tanpa
                # base64 & 60x lebih cepat dari PNG. PNG bytes mentah TIDAK
                # diterima PhotoImage -> TclError -> galeri blank.
                photo = tk.PhotoImage(data=thumb.tobytes("ppm"), width=thumb.width, height=thumb.height)
                self._build_card(i, photo)
            except Exception as e:
                self._build_error_card(i, f"Render gagal: {e}")
        self.frame.update_idletasks()
        self._sync_enabled()
        self._refresh_status()

    def _build_error_card(self, index, message):
        card = tk.Frame(self.frame, bg=SURFACE, highlightthickness=2, highlightbackground=BORDER,
                        highlightcolor=BORDER)
        card.grid(row=index // 4, column=index % 4, padx=12, pady=12, sticky="n")
        card.config(padx=8, pady=8)
        tk.Label(card, text=message, font=FONT_SMALL, bg=SURFACE, fg=DANGER,
                 wraplength=120, justify=tk.CENTER).pack(padx=4, pady=4)
        tk.Label(card, text=str(index + 1), font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED,
                 padx=6, pady=2).pack()

    def _build_card(self, index, photo):
        selected = index in self.tool.selection
        border = ACCENT if selected else BORDER
        card = tk.Frame(self.frame, bg=SURFACE, highlightthickness=2, highlightbackground=border,
                        highlightcolor=border, cursor="hand2")
        card.grid(row=index // 4, column=index % 4, padx=12, pady=12, sticky="n")
        card.config(padx=8, pady=8)

        lbl = tk.Label(card, image=photo, bg=SURFACE, cursor="hand2")
        lbl.image = photo
        lbl.pack()

        num_bg = ACCENT_SOFT if selected else SURFACE
        num_fg = ACCENT if selected else TEXT_MUTED
        num = tk.Label(card, text=str(index + 1), font=FONT_BOLD if selected else FONT_SMALL,
                       bg=num_bg, fg=num_fg, padx=6, pady=2)
        num.pack()

        for w in (card, lbl, num):
            w.bind("<Button-1>", lambda e, i=index: self._toggle(i))
            w.bind("<Enter>", lambda e, c=card: self._card_hover(c, True))
            w.bind("<Leave>", lambda e, c=card: self._card_hover(c, False))

    def _card_hover(self, card, entering):
        if card.cget("highlightbackground") != ACCENT:
            card.config(highlightbackground=ACCENT_SOFT if entering else BORDER)
            card.config(highlightcolor=ACCENT_SOFT if entering else BORDER)

    def _toggle(self, index):
        self.tool.toggle_select(index)
        self._refresh_preview()

    def _require_doc(self):
        if not self.tool.doc:
            messagebox.showinfo("PDF Tool", "Buka PDF dulu.", parent=self.root)
            return False
        return True


def main():
    root = tk.Tk()
    PDFToolApp(root)
    root.mainloop()
