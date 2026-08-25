import tkinter as tk
from tkinter import messagebox, simpledialog
import os
import subprocess
import threading
import fitz
from pdf_ops import PDFTool
from pdf_convert import (
    images_to_pdf,
    office_to_pdf,
    pdf_to_docx,
    html_to_pdf,
    find_soffice,
    OFFICE_EXTS,
)
from pdf_ops import repair_pdf

# ---------- Design tokens (gaya Notion/Linear) ----------
BG          = "#f5f6f8"
SURFACE     = "#ffffff"
SIDEBAR     = "#f0f2f5"
BORDER      = "#e4e7ec"
TEXT        = "#1f2328"
TEXT_MUTED  = "#6b7280"
ACCENT      = "#2f6fed"
ACCENT_HOV  = "#1d5cd6"
ACCENT_SOFT = "#eaf1ff"
HOVER       = "#f0f2f5"
DANGER      = "#dc2626"
DANGER_SOFT = "#fef2f2"
FONT        = ("DejaVu Sans", 10)
FONT_SMALL  = ("DejaVu Sans", 9)
FONT_BOLD   = ("DejaVu Sans", 10, "bold")
FONT_TITLE  = ("DejaVu Sans", 13, "bold")


def _zenity(args, title):
    # Lepaskan grab Tkinter aktif sementara agar tidak terjadi X11 grab deadlock / OS freeze
    grabbed = None
    try:
        if tk._default_root:
            grabbed = tk._default_root.grab_current()
            if grabbed:
                grabbed.grab_release()
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["zenity", "--file-selection", "--title", title] + args,
            capture_output=True, text=True, timeout=600,
        )
        if out.returncode != 0:
            return ""  # Dibatalkan oleh user
        return out.stdout.strip()
    except FileNotFoundError:
        return None  # Zenity tidak terpasang di sistem, fallback ke filedialog
    except subprocess.TimeoutExpired:
        return ""
    finally:
        if grabbed:
            try:
                if grabbed.winfo_exists():
                    _safe_grab(grabbed)
            except Exception:
                pass


def native_open_file(title, pdf_only=True):
    filt = ["--file-filter", "PDF files (*.pdf) | *.pdf *.PDF"] if pdf_only else ["--file-filter", "Semua file | *"]
    r = _zenity(filt, title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        ft = [("PDF files", "*.pdf;*.PDF")] if pdf_only else [("Semua file", "*.*")]
        return filedialog.askopenfilename(title=title, filetypes=ft) or ""
    except Exception:
        return ""


def native_open_files(title):
    r = _zenity(["--multiple", "--separator", "\n"], title)
    if r is not None:
        return [p for p in r.split("\n") if p]
    try:
        from tkinter import filedialog
        files = filedialog.askopenfilenames(title=title, filetypes=[("PDF files", "*.pdf;*.PDF")])
        return list(files) if files else []
    except Exception:
        return []


def native_save_file(title, initial="file.pdf"):
    r = _zenity(["--save", "--filename", initial], title)
    if r is not None:
        if r and not r.lower().endswith(".pdf"):
            r += ".pdf"
        return r
    try:
        from tkinter import filedialog
        res = filedialog.asksaveasfilename(title=title, initialfile=initial, filetypes=[("PDF files", "*.pdf")])
        if res and not res.lower().endswith(".pdf"):
            res += ".pdf"
        return res or ""
    except Exception:
        return ""


def native_open_dir(title):
    r = _zenity(["--directory"], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        return filedialog.askdirectory(title=title) or ""
    except Exception:
        return ""


def native_open_files_images(title):
    r = _zenity(["--multiple", "--separator", "\n",
                 "--file-filter", "Gambar | *.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp",
                 "--file-filter", "Semua file | *"], title)
    if r is not None:
        return [p for p in r.split("\n") if p]
    try:
        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title=title,
            filetypes=[("Gambar", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"), ("Semua file", "*.*")],
        )
        return list(files) if files else []
    except Exception:
        return []


def native_open_file_office(title):
    r = _zenity(["--file-filter",
                 "Dokumen Office | *.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt *.ods *.odp *.rtf *.csv",
                 "--file-filter", "Semua file | *"], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        ft = [("Dokumen Office", "*.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt *.ods *.odp *.rtf *.csv"),
              ("Semua file", "*.*")]
        return filedialog.askopenfilename(title=title, filetypes=ft) or ""
    except Exception:
        return ""


def native_save_file_any(title, initial="output"):
    r = _zenity(["--save", "--filename", initial], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        return filedialog.asksaveasfilename(title=title, initialfile=initial) or ""
    except Exception:
        return ""


def _safe_grab(win):
    try:
        win.grab_set()
    except tk.TclError:
        win.after(50, lambda: _safe_grab(win))
    return win


def _set_app_icon(root):
    """Pakai ikon PNG dari assets/ bila ada; diamkan jika tidak tersedia."""
    try:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
        if os.path.isfile(icon_path):
            root.iconphoto(True, tk.PhotoImage(file=icon_path))
    except Exception:
        pass


class HoverButton(tk.Frame):
    """Tombol flat dengan hover state."""

    def __init__(self, master, text, command=None, primary=False, danger=False, small=False,
                 padx=14, pady=7, tooltip=None):
        bg = ACCENT if primary else (DANGER if danger else SURFACE)
        fg = "#ffffff" if (primary or danger) else TEXT
        hover_bg = ACCENT_HOV if primary else (DANGER if danger else HOVER)
        font = FONT_SMALL if small else FONT
        super().__init__(master, bg=bg, cursor="hand2", highlightthickness=1,
                         highlightbackground=bg, highlightcolor=bg)
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font, cursor="hand2",
                              padx=padx, pady=pady)
        self.label.pack()
        self._state = "normal"
        self._hover_bg = hover_bg
        self._orig_bg = bg
        self._tooltip = tooltip
        self._tip_win = None

        def on_enter(e):
            if self._state == "normal":
                self._set_bg(hover_bg)
                if self._tooltip:
                    self._show_tip(e)

        def on_leave(e):
            self._set_bg(self._orig_bg)
            self._hide_tip()

        def on_click(e):
            if self._state == "normal" and command:
                command()

        self.bind("<Enter>", on_enter)
        self.bind("<Leave>", on_leave)
        self.bind("<Button-1>", on_click)
        self.label.bind("<Enter>", on_enter)
        self.label.bind("<Leave>", on_leave)
        self.label.bind("<Button-1>", on_click)

    def _set_bg(self, bg):
        self.config(bg=bg, highlightbackground=bg, highlightcolor=bg)
        self.label.config(bg=bg)

    def _show_tip(self, e):
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        self._tip_win = tk.Toplevel(self)
        self._tip_win.wm_overrideredirect(True)
        self._tip_win.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip_win, text=self._tooltip, bg="#1f2328", fg="#ffffff",
                 font=FONT_SMALL, padx=8, pady=4).pack()

    def _hide_tip(self):
        if self._tip_win:
            try:
                if self._tip_win.winfo_exists():
                    self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None

    def set_enabled(self, enabled):
        self._state = "normal" if enabled else "disabled"
        bg = "#e9ebef" if not enabled else self._orig_bg
        fg = "#9ca3af" if not enabled else (TEXT if not (self._orig_bg == ACCENT or self._orig_bg == DANGER) else "#ffffff")
        self._set_bg(bg)
        self.label.config(fg=fg)


class SidebarItem(tk.Frame):
    """Item menu sidebar dengan hover."""

    def __init__(self, master, text, command=None, accent=False):
        super().__init__(master, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.text = text
        self.accent = accent
        self.label = tk.Label(self, text=text, bg=SIDEBAR, fg=TEXT,
                              font=FONT, anchor="w", padx=12, pady=8, cursor="hand2")
        self.label.pack(fill=tk.X)
        self._enabled = True
        self.bind("<Enter>", lambda e: self._hover())
        self.bind("<Leave>", lambda e: self._unhover())
        self.bind("<Button-1>", lambda e: self._click())
        self.label.bind("<Enter>", lambda e: self._hover())
        self.label.bind("<Leave>", lambda e: self._unhover())
        self.label.bind("<Button-1>", lambda e: self._click())

    def _hover(self):
        if self._enabled:
            self.config(bg=HOVER)
            self.label.config(bg=HOVER)

    def _unhover(self):
        self.config(bg=SIDEBAR)
        self.label.config(bg=SIDEBAR)

    def _click(self):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        fg = TEXT if enabled else "#9ca3af"
        self.label.config(fg=fg)


class PDFToolApp:
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

    # ---------- Actions ----------
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

    def export_to_image(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        info = f"{n_sel} halaman terpilih." if n_sel else f"Semua halaman ({self.tool.page_count})."

        dlg = tk.Toplevel(self.root)
        dlg.title("Ekspor ke Image")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)

        tk.Label(dlg, text=f"Ekspor: {info}", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(14, 4))

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
        tk.Label(q_row, text="Kualitas JPEG (1-100):", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        quality_entry = tk.Entry(q_row, width=6, font=FONT, relief=tk.FLAT,
                                 highlightthickness=1, highlightbackground=BORDER, state=tk.DISABLED)
        quality_entry.insert(0, "90")
        quality_entry.pack(side=tk.LEFT, padx=(8, 0))

        dpi_row = tk.Frame(dlg, bg=BG)
        dpi_row.pack(fill=tk.X, padx=16, pady=(6, 2))
        tk.Label(dpi_row, text="Resolusi:", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        dpi_var = tk.StringVar(value="150")
        om_dpi = tk.OptionMenu(dpi_row, dpi_var, "96", "150", "300")
        om_dpi.config(font=FONT, bg=SURFACE, relief=tk.FLAT, highlightthickness=1,
                      highlightbackground=BORDER)
        om_dpi.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(dpi_row, text="DPI (150 standar, 300 cetak)", font=FONT_SMALL, bg=BG,
                 fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(8, 0))

        status_lbl = tk.Label(dlg, text="", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED)
        status_lbl.pack(fill=tk.X, padx=16, pady=(2, 0))

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 14))
        btn_cancel = HoverButton(btn_row, "Batal", command=dlg.destroy, small=True)
        btn_cancel.pack(side=tk.LEFT, padx=6)
        btn_export = HoverButton(btn_row, "Pilih Folder & Ekspor",
                    command=lambda: self._do_export_images(dlg, fmt_var, quality_entry, dpi_var),
                    primary=True, small=True)
        btn_export.pack(side=tk.LEFT, padx=6)

    def _do_export_images(self, dlg, fmt_var, quality_entry, dpi_var):
        outdir = native_open_dir("Pilih folder tujuan simpan image")
        if not outdir:
            return None
        try:
            dpi = int(dpi_var.get())
            quality = int(quality_entry.get())
            if not (1 <= quality <= 100):
                raise ValueError
        except ValueError:
            messagebox.showerror("Ekspor Image", "Kualitas JPEG harus angka 1-100.", parent=self.root)
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
            messagebox.showerror("Ekspor Image", f"Gagal ekspor: {e}", parent=self.root)
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

        messagebox.showinfo("Ekspor Image", f"{len(written)} gambar tersimpan di:\n{outdir}", parent=self.root)
        return written

    # ---------- Fitur baru: Edit Teks ----------
    def edit_text(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit & Tambah Teks")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        dlg.geometry("980x680")
        dlg.minsize(860, 580)

        # State dialog
        dlg.current_page = self.tool.selection[0] if self.tool.selection else 0
        dlg.spans = []
        dlg.filtered_spans = []
        dlg.selected_span_idx = None
        dlg.photo_ref = None
        dlg.canvas_scale = 1.0
        dlg.canvas_offset = (0, 0)
        dlg.drag_start = None
        dlg.selected_rect = None

        # Top Bar: Navigasi Halaman
        top_bar = tk.Frame(dlg, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(top_bar, text="Edit & Tambah Teks", font=FONT_BOLD, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=16, pady=10)

        nav_frame = tk.Frame(top_bar, bg=SURFACE)
        nav_frame.pack(side=tk.RIGHT, padx=16, pady=6)

        btn_prev = HoverButton(nav_frame, "\u25c0", command=lambda: self._edit_change_page(dlg, -1), small=True, padx=8, pady=4)
        btn_prev.pack(side=tk.LEFT, padx=2)

        tk.Label(nav_frame, text="Halaman:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=(6, 2))
        page_var = tk.StringVar(value=str(dlg.current_page + 1))
        dlg.page_var = page_var
        page_entry = tk.Entry(nav_frame, textvariable=page_var, width=4, font=FONT_SMALL, justify=tk.CENTER,
                              relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        page_entry.pack(side=tk.LEFT, padx=2)
        total_lbl = tk.Label(nav_frame, text=f"/ {self.tool.page_count}", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED)
        total_lbl.pack(side=tk.LEFT, padx=(2, 6))

        btn_next = HoverButton(nav_frame, "\u25b6", command=lambda: self._edit_change_page(dlg, 1), small=True, padx=8, pady=4)
        btn_next.pack(side=tk.LEFT, padx=2)

        def on_page_submit(e=None):
            try:
                p = int(page_var.get()) - 1
                if 0 <= p < self.tool.page_count:
                    dlg.current_page = p
                    self._load_edit_page(dlg)
                else:
                    page_var.set(str(dlg.current_page + 1))
            except ValueError:
                page_var.set(str(dlg.current_page + 1))

        page_entry.bind("<Return>", on_page_submit)
        page_entry.bind("<FocusOut>", on_page_submit)

        # Tab Switcher
        tab_frame = tk.Frame(dlg, bg=BG)
        tab_frame.pack(fill=tk.X, padx=16, pady=(10, 6))

        content_container = tk.Frame(dlg, bg=BG)
        content_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        frame_digital = tk.Frame(content_container, bg=BG)
        frame_custom = tk.Frame(content_container, bg=BG)
        dlg.frame_digital = frame_digital
        dlg.frame_custom = frame_custom

        tab_mode = tk.StringVar(value="digital")
        dlg.tab_mode = tab_mode

        def switch_tab(mode):
            tab_mode.set(mode)
            if mode == "digital":
                frame_custom.pack_forget()
                frame_digital.pack(fill=tk.BOTH, expand=True)
                tab_btn_digital.config(bg=ACCENT, highlightbackground=ACCENT)
                tab_btn_digital.label.config(bg=ACCENT, fg="#ffffff")
                tab_btn_custom.config(bg=SURFACE, highlightbackground=BORDER)
                tab_btn_custom.label.config(bg=SURFACE, fg=TEXT)
            else:
                frame_digital.pack_forget()
                frame_custom.pack(fill=tk.BOTH, expand=True)
                tab_btn_custom.config(bg=ACCENT, highlightbackground=ACCENT)
                tab_btn_custom.label.config(bg=ACCENT, fg="#ffffff")
                tab_btn_digital.config(bg=SURFACE, highlightbackground=BORDER)
                tab_btn_digital.label.config(bg=SURFACE, fg=TEXT)
                self._render_edit_canvas(dlg)

        tab_btn_digital = HoverButton(tab_frame, "Ganti Teks Digital", command=lambda: switch_tab("digital"), primary=True, small=True)
        tab_btn_digital.pack(side=tk.LEFT, padx=(0, 6))
        dlg.tab_btn_digital = tab_btn_digital

        tab_btn_custom = HoverButton(tab_frame, "Tambah / Timpa Teks Bebas (Untuk PDF Scan & Gambar)", command=lambda: switch_tab("custom"), small=True)
        tab_btn_custom.pack(side=tk.LEFT, padx=6)
        dlg.tab_btn_custom = tab_btn_custom
        dlg.switch_tab = switch_tab

        # ==========================================
        # TAB 1: GANTI TEKS DIGITAL
        # ==========================================
        search_row = tk.Frame(frame_digital, bg=BG)
        search_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(search_row, text="Cari teks:", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))
        search_var = tk.StringVar()
        dlg.search_var = search_var
        search_entry = tk.Entry(search_row, textvariable=search_var, font=FONT_SMALL, relief=tk.FLAT,
                                highlightthickness=1, highlightbackground=BORDER)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search_var.trace_add("write", lambda *args: self._filter_digital_spans(dlg))

        digital_body = tk.Frame(frame_digital, bg=BG)
        digital_body.pack(fill=tk.BOTH, expand=True)
        dlg.digital_body = digital_body

        # 1. Empty Notice Frame (for scanned / image PDFs)
        empty_notice = tk.Frame(digital_body, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, padx=24, pady=28)
        tk.Label(empty_notice, text="Dokumen Hasil Scan / Gambar", font=FONT_BOLD, bg=SURFACE, fg=TEXT).pack(pady=(0, 6))
        tk.Label(empty_notice,
                 text="Tidak ditemukan layer teks digital pada halaman ini karena halaman merupakan gambar atau hasil scan.\n"
                      "Untuk mengedit, mengganti, atau menambahkan tulisan pada dokumen scan ini, gunakan mode 'Tambah / Timpa Teks Bebas'.",
                 font=FONT, bg=SURFACE, fg=TEXT_MUTED, justify=tk.CENTER, wraplength=520).pack(pady=4)
        HoverButton(empty_notice, "Buka Mode Tambah / Timpa Teks Bebas \u2794", command=lambda: switch_tab("custom"), primary=True).pack(pady=(14, 0))
        dlg.empty_notice = empty_notice

        # 2. Spans List View Frame (for digital PDFs)
        spans_view = tk.Frame(digital_body, bg=BG)
        dlg.spans_view = spans_view

        list_frame = tk.Frame(spans_view, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        listbox = tk.Listbox(list_frame, font=FONT_SMALL, bg=SURFACE, fg=TEXT,
                             selectbackground=ACCENT_SOFT, selectforeground=TEXT,
                             relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER,
                             activestyle="none")
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview, bg=BORDER,
                          activebackground="#c8ccd2", troughcolor=BG, relief=tk.FLAT)
        listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dlg.listbox = listbox

        # Detail & Edit Box
        edit_box = tk.Frame(spans_view, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=10)
        edit_box.pack(fill=tk.X, pady=(4, 0))

        info_label = tk.Label(edit_box, text="Pilih salah satu baris teks di atas untuk diedit.", font=FONT_SMALL,
                              bg=SURFACE, fg=TEXT_MUTED, anchor="w", justify=tk.LEFT)
        info_label.pack(fill=tk.X, pady=(0, 6))
        dlg.info_label = info_label

        tk.Label(edit_box, text="Teks baru:", font=FONT_BOLD, bg=SURFACE, fg=TEXT, anchor="w").pack(fill=tk.X)
        entry_new_text = tk.Entry(edit_box, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_new_text.pack(fill=tk.X, pady=(2, 6))
        dlg.entry_new_text = entry_new_text

        opts_row = tk.Frame(edit_box, bg=SURFACE)
        opts_row.pack(fill=tk.X, pady=(0, 8))

        auto_shrink_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts_row, text="Sesuaikan ukuran font otomatis jika teks lebih panjang (Auto-shrink)",
                       variable=auto_shrink_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT)

        whiteout_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts_row, text="Tutup teks lama dengan warna putih",
                       variable=whiteout_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT, padx=12)

        btn_row_digital = tk.Frame(edit_box, bg=SURFACE)
        btn_row_digital.pack(fill=tk.X)

        HoverButton(btn_row_digital, "Terapkan Perubahan",
                    command=lambda: self._do_digital_replace(dlg, entry_new_text, auto_shrink_var.get(), whiteout_var.get()),
                    primary=True, small=True).pack(side=tk.RIGHT, padx=4)

        HoverButton(btn_row_digital, "Ganti Semua yang Sama",
                    command=lambda: self._do_digital_replace_all(dlg, entry_new_text, auto_shrink_var.get(), whiteout_var.get()),
                    small=True).pack(side=tk.RIGHT, padx=4)

        HoverButton(btn_row_digital, "Kosongkan / Hapus Teks",
                    command=lambda: self._do_digital_clear(dlg),
                    danger=True, small=True).pack(side=tk.LEFT, padx=4)

        listbox.bind("<<ListboxSelect>>", lambda e: self._on_digital_span_select(dlg))

        # ==========================================
        # TAB 2: TAMBAH / TIMPA TEKS BEBAS (SCAN & DIGITAL)
        # ==========================================
        split_frame = tk.Frame(frame_custom, bg=BG)
        split_frame.pack(fill=tk.BOTH, expand=True)

        # Left Column: Canvas Preview
        preview_col = tk.Frame(split_frame, bg=BG)
        preview_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        canvas_header = tk.Frame(preview_col, bg=BG)
        canvas_header.pack(fill=tk.X, pady=(0, 4))
        tk.Label(canvas_header, text="1. Klik dokumen untuk tentukan posisi teks:", font=FONT_BOLD, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        tk.Label(canvas_header, text="(atau seret kotak)", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=4)

        canvas_container = tk.Frame(preview_col, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        canvas_container.pack(fill=tk.BOTH, expand=True)

        canvas_page = tk.Canvas(canvas_container, bg="#e5e7eb", highlightthickness=0, cursor="crosshair")
        canvas_page.pack(fill=tk.BOTH, expand=True)
        dlg.canvas_page = canvas_page

        # Right Column: Controls Form
        form_col = tk.Frame(split_frame, bg=SURFACE, width=380, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=12)
        form_col.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(8, 0))
        form_col.pack_propagate(False)

        tk.Label(form_col, text="2. Masukkan teks & atur gaya:", font=FONT_BOLD, bg=SURFACE, fg=TEXT, anchor="w").pack(fill=tk.X, pady=(0, 6))

        tk.Label(form_col, text="Teks yang akan dimasukkan:", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X)
        text_custom_entry = tk.Text(form_col, height=4, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        text_custom_entry.pack(fill=tk.X, pady=(2, 8))

        pos_row = tk.Frame(form_col, bg=SURFACE)
        pos_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(pos_row, text="Posisi X:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        x_var = tk.StringVar(value="72")
        entry_x = tk.Entry(pos_row, textvariable=x_var, width=6, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_x.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(pos_row, text="Y:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        y_var = tk.StringVar(value="100")
        entry_y = tk.Entry(pos_row, textvariable=y_var, width=6, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_y.pack(side=tk.LEFT, padx=(4, 6))
        tk.Label(pos_row, text="pt", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED).pack(side=tk.LEFT)

        f_row1 = tk.Frame(form_col, bg=SURFACE)
        f_row1.pack(fill=tk.X, pady=(0, 6))
        tk.Label(f_row1, text="Font:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        font_family_var = tk.StringVar(value="Helvetica (Sans)")
        om_font = tk.OptionMenu(f_row1, font_family_var, "Helvetica (Sans)", "Times (Serif)", "Courier (Mono)")
        om_font.config(font=FONT_SMALL, bg=SURFACE, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_font.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(f_row1, text="Ukuran:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        size_var = tk.StringVar(value="12")
        om_size = tk.Spinbox(f_row1, from_=6, to=72, textvariable=size_var, width=4, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_size.pack(side=tk.LEFT, padx=(4, 0))

        f_row2 = tk.Frame(form_col, bg=SURFACE)
        f_row2.pack(fill=tk.X, pady=(0, 8))
        bold_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_row2, text="Tebal", variable=bold_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT)
        italic_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_row2, text="Miring", variable=italic_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT, padx=8)

        tk.Label(f_row2, text="Warna:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=(8, 4))
        color_var = tk.StringVar(value="Hitam")
        om_color = tk.OptionMenu(f_row2, color_var, "Hitam", "Biru", "Merah", "Hijau", "Putih", "Abu-abu")
        om_color.config(font=FONT_SMALL, bg=SURFACE, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_color.pack(side=tk.LEFT)

        whiteout_custom_var = tk.BooleanVar(value=True)
        tk.Checkbutton(form_col, text="Tutup latar belakang dengan warna putih (Whiteout/Timpa tulisan scan)",
                       variable=whiteout_custom_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE,
                       wraplength=340, justify=tk.LEFT).pack(anchor="w", pady=(0, 10))

        custom_action_frame = tk.Frame(form_col, bg=SURFACE)
        custom_action_frame.pack(fill=tk.X, pady=(4, 0))

        HoverButton(custom_action_frame, "Sisipkan Teks ke Dokumen",
                    command=lambda: self._do_insert_custom_text(
                        dlg, text_custom_entry, x_var, y_var, size_var, font_family_var,
                        bold_var, italic_var, color_var, whiteout_custom_var
                    ),
                    primary=True).pack(fill=tk.X, pady=(0, 6))

        HoverButton(custom_action_frame, "Tutup Area Saja (Whiteout)",
                    command=lambda: self._do_whiteout_only(dlg, x_var, y_var),
                    small=True).pack(fill=tk.X)

        status_custom_lbl = tk.Label(form_col, text="", font=FONT_SMALL, bg=SURFACE, fg=ACCENT, wraplength=340)
        status_custom_lbl.pack(fill=tk.X, pady=(8, 0))
        dlg.status_custom_lbl = status_custom_lbl

        # Canvas Mouse Interaction
        def on_canvas_click(e):
            dlg.drag_start = (e.x, e.y)
            canvas_page.delete("rubberband")

        def on_canvas_drag(e):
            if not dlg.drag_start:
                return
            canvas_page.delete("rubberband")
            canvas_page.create_rectangle(dlg.drag_start[0], dlg.drag_start[1], e.x, e.y,
                                         outline=ACCENT, dash=(2, 2), width=1.5, tags="rubberband")

        def on_canvas_release(e):
            if not dlg.drag_start:
                return
            x0, y0 = dlg.drag_start
            x1, y1 = e.x, e.y
            canvas_page.delete("rubberband")
            canvas_page.delete("marker")
            scale = getattr(dlg, "canvas_scale", 1.0)
            if scale <= 0:
                scale = 1.0
            ox, oy = getattr(dlg, "canvas_offset", (0, 0))

            if not self.tool.doc or dlg.current_page >= self.tool.page_count:
                return
            page = self.tool.doc.load_page(dlg.current_page)
            pw, ph = page.rect.width, page.rect.height

            if abs(x1 - x0) < 5 and abs(y1 - y0) < 5:
                # Single Click
                pdf_x = max(0.0, min(pw, (x0 - ox) / scale))
                pdf_y = max(0.0, min(ph, (y0 - oy) / scale))
                x_var.set(str(round(pdf_x, 1)))
                y_var.set(str(round(pdf_y, 1)))
                dlg.selected_rect = None
                canvas_page.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4, fill=DANGER, outline="#ffffff", width=1.5, tags="marker")
                status_custom_lbl.config(text=f"Posisi dipilih: ({pdf_x:.1f}, {pdf_y:.1f}) pt", fg=TEXT_MUTED)
            else:
                # Drag Box
                min_x = min(x0, x1)
                max_x = max(x0, x1)
                min_y = min(y0, y1)
                max_y = max(y0, y1)
                pdf_x = max(0.0, min(pw, (min_x - ox) / scale))
                pdf_y = max(0.0, min(ph, (min_y - oy) / scale))
                box_w = max(1.0, (max_x - min_x) / scale)
                box_h = max(1.0, (max_y - min_y) / scale)
                x_var.set(str(round(pdf_x, 1)))
                y_var.set(str(round(pdf_y, 1)))
                dlg.selected_rect = (pdf_x, pdf_y, box_w, box_h)
                canvas_page.create_rectangle(min_x, min_y, max_x, max_y, outline=DANGER, width=1.5, tags="marker")
                status_custom_lbl.config(text=f"Area dipilih: ({pdf_x:.1f}, {pdf_y:.1f}) ukuran {box_w:.1f}x{box_h:.1f} pt", fg=TEXT_MUTED)
            dlg.drag_start = None

        canvas_page.bind("<Button-1>", on_canvas_click)
        canvas_page.bind("<B1-Motion>", on_canvas_drag)
        canvas_page.bind("<ButtonRelease-1>", on_canvas_release)
        canvas_page.bind("<Configure>", lambda e: self._render_edit_canvas(dlg) if dlg.tab_mode.get() == "custom" else None)

        # Initial Tab & Load
        switch_tab("digital")
        self._load_edit_page(dlg)

    def _edit_change_page(self, dlg, delta):
        if not self.tool.doc:
            return
        new_page = dlg.current_page + delta
        if 0 <= new_page < self.tool.page_count:
            dlg.current_page = new_page
            self._load_edit_page(dlg)

    def _load_edit_page(self, dlg):
        pno = dlg.current_page
        dlg.page_var.set(str(pno + 1))
        dlg.selected_span_idx = None
        dlg.selected_rect = None

        # Muat span teks digital
        try:
            spans = self.tool.get_text_spans(pno)
        except Exception:
            spans = []
        dlg.spans = spans

        self._filter_digital_spans(dlg)

        if len(spans) == 0:
            dlg.empty_notice.pack(fill=tk.BOTH, expand=True)
            dlg.spans_view.pack_forget()
        else:
            dlg.empty_notice.pack_forget()
            dlg.spans_view.pack(fill=tk.BOTH, expand=True)

        if dlg.tab_mode.get() == "custom":
            self._render_edit_canvas(dlg)

    def _filter_digital_spans(self, dlg):
        query = dlg.search_var.get().lower().strip()
        dlg.listbox.delete(0, tk.END)
        dlg.filtered_spans = []
        for orig_idx, s in enumerate(dlg.spans):
            if not query or query in s["text"].lower():
                dlg.filtered_spans.append((orig_idx, s))
                label = f"{s['text'][:55]}  |  {s['font']}  |  {s['size']:.1f}pt"
                dlg.listbox.insert(tk.END, label)

        if len(dlg.spans) > 0:
            dlg.info_label.config(
                text=f"{len(dlg.filtered_spans)} dari {len(dlg.spans)} span teks di halaman {dlg.current_page + 1}. Pilih salah satu untuk diedit.",
                fg=TEXT_MUTED,
            )
        dlg.entry_new_text.delete(0, tk.END)

    def _on_digital_span_select(self, dlg):
        sel = dlg.listbox.curselection()
        if not sel:
            return
        idx_in_filtered = sel[0]
        if idx_in_filtered >= len(dlg.filtered_spans):
            return
        orig_idx, s = dlg.filtered_spans[idx_in_filtered]
        dlg.selected_span_idx = orig_idx
        dlg.info_label.config(
            text=f"Font: {s['font']}  \u00b7  Ukuran: {s['size']:.1f}pt  \u00b7  "
                 f"Warna: #{s['color']:06x}  \u00b7  Posisi: ({s['bbox'].x0:.1f}, {s['bbox'].y0:.1f}) pt",
            fg=TEXT,
        )
        dlg.entry_new_text.delete(0, tk.END)
        dlg.entry_new_text.insert(0, s["text"])

    def _do_digital_replace(self, dlg, entry_new_text, auto_shrink, whiteout):
        if dlg.selected_span_idx is None:
            messagebox.showinfo("Edit Teks", "Pilih teks yang mau diganti terlebih dahulu.", parent=dlg)
            return
        new_text = entry_new_text.get()
        if not new_text:
            messagebox.showinfo("Edit Teks", "Teks baru tidak boleh kosong.", parent=dlg)
            return
        try:
            result = self.tool.replace_text(
                dlg.current_page,
                dlg.selected_span_idx,
                new_text,
                auto_shrink=auto_shrink,
                allow_longer=True,
                whiteout=whiteout,
            )
        except ValueError as e:
            messagebox.showerror("Edit Teks", str(e), parent=dlg)
            return

        msg = "Teks berhasil diganti."
        if result.get("warnings"):
            msg += "\n\nCatatan:\n- " + "\n- ".join(result["warnings"])
        messagebox.showinfo("Edit Teks", msg, parent=dlg)

        self._load_edit_page(dlg)
        self._refresh_preview()

    def _do_digital_replace_all(self, dlg, entry_new_text, auto_shrink, whiteout):
        if dlg.selected_span_idx is None or dlg.selected_span_idx >= len(dlg.spans):
            messagebox.showinfo("Edit Teks", "Pilih teks yang mau diganti terlebih dahulu.", parent=dlg)
            return
        orig_s = dlg.spans[dlg.selected_span_idx]
        old_text = orig_s["text"]
        new_text = entry_new_text.get()
        if not new_text:
            messagebox.showinfo("Edit Teks", "Teks baru tidak boleh kosong.", parent=dlg)
            return
        if not messagebox.askyesno("Ganti Semua", f"Ganti semua kemunculan '{old_text}' di halaman ini dengan '{new_text}'?", parent=dlg):
            return
        try:
            result = self.tool.replace_all_text(
                dlg.current_page,
                old_text,
                new_text,
                auto_shrink=auto_shrink,
                whiteout=whiteout,
            )
        except ValueError as e:
            messagebox.showerror("Edit Teks", str(e), parent=dlg)
            return

        msg = f"Berhasil mengganti {result['count']} teks."
        if result.get("warnings"):
            msg += "\n\nCatatan:\n- " + "\n- ".join(result["warnings"])
        messagebox.showinfo("Edit Teks", msg, parent=dlg)

        self._load_edit_page(dlg)
        self._refresh_preview()

    def _do_digital_clear(self, dlg):
        if dlg.selected_span_idx is None or dlg.selected_span_idx >= len(dlg.spans):
            messagebox.showinfo("Edit Teks", "Pilih teks yang mau dihapus terlebih dahulu.", parent=dlg)
            return
        if not messagebox.askyesno("Hapus Teks", "Hapus/tutup teks terpilih dengan warna putih?", parent=dlg):
            return
        s = dlg.spans[dlg.selected_span_idx]
        try:
            self.tool.whiteout_area(dlg.current_page, s["bbox"])
        except Exception as e:
            messagebox.showerror("Edit Teks", f"Gagal menghapus teks: {e}", parent=dlg)
            return
        messagebox.showinfo("Edit Teks", "Teks berhasil dihapus.", parent=dlg)
        self._load_edit_page(dlg)
        self._refresh_preview()

    def _render_edit_canvas(self, dlg):
        canvas = dlg.canvas_page
        canvas.delete("all")
        page_idx = dlg.current_page
        if not self.tool.doc or page_idx < 0 or page_idx >= self.tool.page_count:
            return
        page = self.tool.doc.load_page(page_idx)
        pw, ph = page.rect.width, page.rect.height

        canvas.update_idletasks()
        cw = max(canvas.winfo_width(), 360)
        ch = max(canvas.winfo_height(), 460)

        scale = min((cw - 24) / pw, (ch - 24) / ph)
        scale = max(0.1, min(2.0, scale))
        dlg.canvas_scale = scale

        target_w = int(pw * scale)
        target_h = int(ph * scale)

        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        photo = tk.PhotoImage(data=pix.tobytes("ppm"), width=pix.width, height=pix.height)
        dlg.photo_ref = photo

        ox = max(12, (cw - target_w) // 2)
        oy = max(12, (ch - target_h) // 2)
        dlg.canvas_offset = (ox, oy)

        canvas.create_image(ox, oy, image=photo, anchor="nw", tags="page_img")

    def _do_insert_custom_text(self, dlg, text_widget, x_var, y_var, size_var, font_var, bold_var, italic_var, color_var, whiteout_var):
        text = text_widget.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("Tambah Teks", "Ketik teks yang ingin dimasukkan terlebih dahulu.", parent=dlg)
            return
        try:
            x = float(x_var.get())
            y = float(y_var.get())
        except ValueError:
            messagebox.showerror("Tambah Teks", "Posisi X dan Y harus berupa angka.", parent=dlg)
            return
        try:
            fontsize = float(size_var.get())
        except ValueError:
            fontsize = 12.0

        color_map = {
            "Hitam": (0.0, 0.0, 0.0),
            "Biru": (0.1, 0.3, 0.8),
            "Merah": (0.85, 0.15, 0.15),
            "Hijau": (0.1, 0.6, 0.2),
            "Putih": (1.0, 1.0, 1.0),
            "Abu-abu": (0.45, 0.45, 0.45),
        }
        color = color_map.get(color_var.get(), (0.0, 0.0, 0.0))

        font_map = {
            "Helvetica (Sans)": "helv",
            "Times (Serif)": "times",
            "Courier (Mono)": "courier",
        }
        font_family = font_map.get(font_var.get(), "helv")

        box_w, box_h = None, None
        if hasattr(dlg, "selected_rect") and dlg.selected_rect:
            box_w, box_h = dlg.selected_rect[2], dlg.selected_rect[3]

        try:
            self.tool.add_custom_text(
                dlg.current_page,
                text,
                x=x,
                y=y,
                fontsize=fontsize,
                font_family=font_family,
                color=color,
                bold=bold_var.get(),
                italic=italic_var.get(),
                whiteout=whiteout_var.get(),
                box_width=box_w,
                box_height=box_h,
            )
        except Exception as e:
            messagebox.showerror("Tambah Teks", f"Gagal menambahkan teks: {e}", parent=dlg)
            return

        dlg.status_custom_lbl.config(text="\u2713 Teks berhasil disisipkan.", fg="#16a34a")
        dlg.selected_rect = None
        self._load_edit_page(dlg)
        self._render_edit_canvas(dlg)
        self._refresh_preview()

    def _do_whiteout_only(self, dlg, x_var, y_var):
        try:
            x = float(x_var.get())
            y = float(y_var.get())
        except ValueError:
            messagebox.showerror("Whiteout", "Posisi X dan Y harus berupa angka.", parent=dlg)
            return

        if hasattr(dlg, "selected_rect") and dlg.selected_rect:
            w, h = dlg.selected_rect[2], dlg.selected_rect[3]
            rect = fitz.Rect(x, y, x + w, y + h)
        else:
            rect = fitz.Rect(x, y, x + 150, y + 25)

        try:
            self.tool.whiteout_area(dlg.current_page, rect)
        except Exception as e:
            messagebox.showerror("Whiteout", f"Gagal menutup area: {e}", parent=dlg)
            return

        dlg.status_custom_lbl.config(text=f"\u2713 Area ({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f}) berhasil ditutup putih.", fg="#16a34a")
        dlg.selected_rect = None
        self._load_edit_page(dlg)
        self._render_edit_canvas(dlg)
        self._refresh_preview()

    # ---------- Fitur baru: Compress ----------
    def compress_pdf(self):
        if not self._require_doc():
            return
        path = native_save_file("Simpan PDF terkompresi", initial="compressed.pdf")
        if not path:
            return
        try:
            before = os.path.getsize(self.tool.path)
            self.tool.compress(path)
            after = os.path.getsize(path)
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal kompres: {e}", parent=self.root)
            return
        pct = (1 - after / before) * 100 if before else 0
        messagebox.showinfo(
            "PDF Tool",
            f"Kompresi selesai.\n{before / 1024:.1f} KB \u2192 {after / 1024:.1f} KB "
            f"({pct:.1f}% lebih kecil)\n\nTersimpan: {path}",
            parent=self.root,
        )

    # ---------- Fitur baru: Nomor Halaman ----------
    def page_numbers(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Nomor Halaman")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Mulai dari nomor:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 2))
        start = tk.Entry(dlg, width=8, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        start.insert(0, "1")
        start.pack(anchor="w", padx=16)
        tk.Label(dlg, text="Posisi:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(8, 2))
        pos = tk.StringVar(value="bottom")
        for text, val in [("Bawah tengah", "bottom"), ("Atas tengah", "top"),
                          ("Bawah kanan", "bottom-right"), ("Bawah kiri", "bottom-left")]:
            tk.Radiobutton(dlg, text=text, variable=pos, value=val, bg=BG, fg=TEXT, font=FONT,
                           activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Terapkan", command=lambda: self._do_page_numbers(dlg, start, pos),
                    primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_page_numbers(self, dlg, start_entry, pos_var):
        try:
            start = int(start_entry.get())
        except ValueError:
            messagebox.showerror("PDF Tool", "Nomor awal harus angka.", parent=dlg)
            return
        try:
            self.tool.add_page_numbers(start=start, position=pos_var.get())
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Gagal: {e}", parent=dlg)
            return
        dlg.destroy()
        self._refresh_preview()

    # ---------- Fitur baru: Balik urutan ----------
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

    # ---------- Fitur baru: Stempel / tanda tangan ----------
    def stamp_image_ui(self):
        if not self._require_doc():
            return
        default_page = (self.tool.selection[0] + 1) if self.tool.selection else 1
        img_path = tk.StringVar()
        dlg = tk.Toplevel(self.root)
        dlg.title("Stempel / Tanda Tangan Gambar")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Gambar (PNG/JPG tanda tangan / stempel):", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 2))
        r1 = tk.Frame(dlg, bg=BG)
        r1.pack(fill=tk.X, padx=16)
        tk.Entry(r1, textvariable=img_path, font=FONT, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER).pack(side=tk.LEFT, fill=tk.X, expand=True)
        HoverButton(r1, "\u2026", command=lambda: img_path.set(native_open_file("Pilih gambar stempel", pdf_only=False)), small=True).pack(side=tk.LEFT, padx=(6, 0))
        r2 = tk.Frame(dlg, bg=BG)
        r2.pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(r2, text="Halaman:", font=FONT_SMALL, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        page_var = tk.StringVar(value=str(default_page))
        tk.Entry(r2, textvariable=page_var, width=4, font=FONT_SMALL, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER, justify=tk.CENTER).pack(side=tk.LEFT, padx=(4, 14))
        tk.Label(r2, text=f"(maks {self.tool.page_count})", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)

        def do_stamp():
            p = img_path.get().strip()
            if not p:
                messagebox.showinfo("Stempel", "Pilih file gambar dulu.", parent=dlg)
                return
            try:
                pno = int(page_var.get()) - 1
                if not (0 <= pno < self.tool.page_count):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Stempel", "Nomor halaman tidak valid.", parent=dlg)
                return
            dlg.destroy()
            try:
                self.tool.stamp_image(pno, p)
            except ValueError as e:
                messagebox.showerror("Stempel", str(e), parent=self.root)
                return
            self._refresh_preview()

        HoverButton(btn_row, "Tempel di kanan-bawah", command=do_stamp, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- Fitur baru: Metadata ----------
    def metadata_ui(self):
        if not self._require_doc():
            return
        meta = self.tool.get_metadata()
        size = meta.get("_file_size")
        dlg = tk.Toplevel(self.root)
        dlg.title("Info Dokumen (Metadata)")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        head = f"{os.path.basename(self.tool.path or '')}  \u00b7  {meta.get('_page_count', 0)} halaman"
        if size:
            head += f"  \u00b7  {size / 1024:.1f} KB"
        if meta.get("_encrypted"):
            head += "  \u00b7  \U0001f512 terenkripsi"
        tk.Label(dlg, text=head, font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 6))
        fields = [("title", "Judul"), ("author", "Penulis"), ("subject", "Subjek"),
                  ("keywords", "Kata kunci"), ("creator", "Aplikasi pembuat")]
        entries = {}
        for i, (key, label) in enumerate(fields):
            tk.Label(dlg, text=f"{label}:", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16, pady=(6, 0))
            e = tk.Entry(dlg, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
            e.insert(0, meta.get(key) or "")
            e.pack(fill=tk.X, padx=16)
            entries[key] = e
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(14, 12))

        def do_save():
            kwargs = {}
            for key, e in entries.items():
                cur = meta.get(key) or ""
                if e.get() != cur:
                    kwargs[key] = e.get()
            dlg.destroy()
            if not kwargs:
                return
            try:
                self.tool.set_metadata(**kwargs)
            except Exception as ex:
                messagebox.showerror("Metadata", f"Gagal: {ex}", parent=self.root)
                return
            messagebox.showinfo("Metadata", "Metadata diperbarui.\nGunakan Simpan untuk menyimpan ke file.", parent=self.root)

        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Terapkan", command=do_save, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- Fitur baru: Unlock password ----------
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

    # ---------- Fitur baru: Image -> PDF ----------
    def images_to_pdf_ui(self):
        paths = native_open_files_images("Pilih gambar (bisa banyak)")
        if not paths:
            return
        out = native_save_file_any("Simpan PDF dari gambar", initial="gambar.pdf")
        if not out:
            return
        mode_var = tk.StringVar(value="a4")
        dlg = tk.Toplevel(self.root)
        dlg.title("Image \u2192 PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text=f"{len(paths)} gambar dipilih.", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg, text="Ukuran halaman:", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(anchor="w", padx=16)
        tk.Radiobutton(dlg, text="A4 (potret, gambar otomatis pas)", variable=mode_var, value="a4",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)
        tk.Radiobutton(dlg, text="Mengikuti ukuran gambar", variable=mode_var, value="auto",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=24)

        def do_convert():
            mode = mode_var.get()
            dlg.destroy()
            try:
                images_to_pdf(paths, out, page_mode=mode)
            except ValueError as e:
                messagebox.showerror("Image \u2192 PDF", str(e), parent=self.root)
                return
            messagebox.showinfo("Image \u2192 PDF", f"Selesai!\n{len(paths)} gambar jadi PDF:\n{out}", parent=self.root)

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(10, 12))
        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Buat PDF", command=do_convert, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- Fitur baru: Office -> PDF ----------
    def office_to_pdf_ui(self):
        path = native_open_file_office("Pilih dokumen Word/Excel/PPT")
        if not path:
            return
        if not find_soffice():
            messagebox.showerror(
                "Office \u2192 PDF",
                "LibreOffice tidak ditemukan di sistem ini.\n\n"
                "Pasang dengan:\n  sudo apt install libreoffice",
                parent=self.root,
            )
            return
        outdir = native_open_dir("Folder tujuan PDF hasil konversi")
        if not outdir:
            return
        self.root.config(cursor="watch")
        self.status_label.config(text=f"Mengonversi {os.path.basename(path)} \u2026", fg=ACCENT)
        self.root.update_idletasks()
        try:
            result = office_to_pdf(path, outdir)
        except ValueError as e:
            messagebox.showerror("Office \u2192 PDF", str(e), parent=self.root)
            return
        finally:
            self.root.config(cursor="")
        self._refresh_status()
        messagebox.showinfo("Office \u2192 PDF", f"Konversi selesai:\n{result}", parent=self.root)

    # ---------- Fitur baru: PDF -> Word ----------
    def pdf_to_word_ui(self):
        path = native_open_file("Pilih PDF sumber") if not (self.tool.path and self.tool.doc) else None
        src = path or self.tool.path
        if not src:
            messagebox.showinfo("PDF \u2192 Word", "Buka atau pilih PDF dulu.", parent=self.root)
            return
        initial = os.path.splitext(os.path.basename(src))[0] + ".docx"
        out = native_save_file_any("Simpan dokumen Word", initial=initial)
        if not out:
            return
        self.root.config(cursor="watch")
        self.status_label.config(text="Mengonversi ke Word \u2026", fg=ACCENT)
        self.root.update_idletasks()
        try:
            n = pdf_to_docx(src, out)
        except ValueError as e:
            messagebox.showerror("PDF \u2192 Word", str(e), parent=self.root)
            return
        except Exception as e:
            messagebox.showerror("PDF \u2192 Word", f"Gagal: {e}", parent=self.root)
            return
        finally:
            self.root.config(cursor="")
        self._refresh_status()
        messagebox.showinfo(
            "PDF \u2192 Word",
            f"Selesai ({n} halaman).\nCatatan: hasil best-effort berupa teks per paragraf,\nlayout kompleks tidak identik.\n\nTersimpan: {out}",
            parent=self.root,
        )

    # ---------- Fitur baru: HTML -> PDF ----------
    def html_to_pdf_ui(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("HTML \u2192 PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        dlg.geometry("520x420")
        tk.Label(dlg, text="Tempel kode HTML:", font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 4))
        txt = tk.Text(dlg, font=("DejaVu Sans Mono", 9), relief=tk.FLAT,
                      highlightthickness=1, highlightbackground=BORDER)
        txt.insert("1.0", "<h1>Judul</h1>\n<p>Tulis <b>HTML</b> di sini.</p>")
        txt.pack(fill=tk.BOTH, expand=True, padx=16)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=10)

        def do_convert():
            html = txt.get("1.0", tk.END)
            if not html.strip():
                messagebox.showinfo("HTML \u2192 PDF", "HTML masih kosong.", parent=dlg)
                return
            out = native_save_file_any("Simpan PDF dari HTML", initial="dokumen.pdf")
            if not out:
                return
            dlg.destroy()
            try:
                html_to_pdf(html, out)
            except ValueError as e:
                messagebox.showerror("HTML \u2192 PDF", str(e), parent=self.root)
                return
            messagebox.showinfo("HTML \u2192 PDF", f"Selesai:\n{out}", parent=self.root)

        HoverButton(btn_row, "Batal", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Buat PDF", command=do_convert, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- Fitur baru: PDF -> Text ----------
    def export_text_ui(self):
        if not self._require_doc():
            return
        pages = list(self.tool.selection) if self.tool.selection else None
        base = os.path.splitext(os.path.basename(self.tool.path))[0] if self.tool.path else "dokumen"
        out = native_save_file_any("Simpan teks (.txt)", initial=base + ".txt")
        if not out:
            return
        try:
            self.tool.export_text(out, pages=pages)
        except Exception as e:
            messagebox.showerror("PDF \u2192 Text", f"Gagal: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF \u2192 Text", f"Teks tersimpan:\n{out}", parent=self.root)

    # ---------- Fitur baru: Ekstrak gambar ----------
    def extract_images_ui(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        pages = list(self.tool.selection) if n_sel else list(range(self.tool.page_count))
        outdir = native_open_dir("Pilih folder tujuan gambar")
        if not outdir:
            return
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            written = self.tool.extract_images(pages, outdir)
        except ValueError as e:
            messagebox.showerror("Ekstrak Gambar", str(e), parent=self.root)
            return
        finally:
            self.root.config(cursor="")
        if written:
            messagebox.showinfo("Ekstrak Gambar", f"{len(written)} gambar tersimpan di:\n{outdir}", parent=self.root)
        else:
            messagebox.showinfo("Ekstrak Gambar", "Tidak ada gambar tertanam yang ditemukan.", parent=self.root)

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


def main():
    root = tk.Tk()
    PDFToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()