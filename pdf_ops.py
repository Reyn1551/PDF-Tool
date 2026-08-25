import fitz
import os
import math
import glob

# ---------- Fallback font (untuk edit teks) ----------
_FONT_ROOTS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]


def _find_system_font(bold=False, italic=False, serif=False, mono=False):
    """Cari font TTF/OTF di sistem yang cocok dengan gaya. Return path atau None."""
    if mono:
        names = ["DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"]
    elif serif:
        names = [
            "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf",
            "LiberationSerif-Bold.ttf" if bold else "LiberationSerif-Regular.ttf",
        ]
    else:
        names = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
        ]
    for root in _FONT_ROOTS:
        for name in names:
            for sub in ("truetype", "opentype", ""):
                p = os.path.join(root, sub, name) if sub else os.path.join(root, name)
                if os.path.isfile(p):
                    return p
    for root in _FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for p in glob.glob(os.path.join(root, "**", "*.ttf"), recursive=True):
            if os.path.basename(p).lower() in (n.lower() for n in names):
                return p
    return None


def _base14_code(bold=False, italic=False, serif=False, mono=False):
    """Kode Base-14 PyMuPDF sesuai gaya (fallback terakhir)."""
    key = (bool(bold), bool(italic))
    if mono:
        return {(False, False): "cour", (True, False): "cobo", (False, True): "coit", (True, True): "cobi"}[key]
    if serif:
        return {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"}[key]
    return {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}[key]


class PDFTool:
    def __init__(self):
        self.doc = None
        self.path = None
        self._selection = set()
        self.was_encrypted = False  # dokumen terenkripsi saat dibuka?

    def open(self, path, password=None):
        try:
            doc = fitz.open(path)
        except Exception as e:
            raise ValueError(f"Gagal membuka PDF: {e}")
        encrypted = bool(doc.needs_pass)
        if doc.needs_pass:
            if not password:
                doc.close()
                raise ValueError("NEEDS_PASSWORD")
            if not doc.authenticate(password):
                doc.close()
                raise ValueError("Password salah.")
        self.close()
        self.doc = doc
        self.path = path
        self._selection = set()
        self.was_encrypted = encrypted

    def close(self):
        if self.doc:
            self.doc.close()
            self.doc = None
            self.path = None
            self._selection = set()

    @property
    def page_count(self):
        return self.doc.page_count if self.doc else 0

    @property
    def selection(self):
        return sorted(self._selection)

    def toggle_select(self, index):
        if index in self._selection:
            self._selection.remove(index)
        else:
            self._selection.add(index)
        return self.selection

    def clear_selection(self):
        self._selection.clear()

    def select_all(self):
        self._selection = set(range(self.page_count))

    def get_thumb(self, index, max_width=140):
        page = self.doc.load_page(index)
        w = page.rect.width or 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(max_width / w, max_width / w))
        return pix

    def delete_pages(self, indexes):
        if not indexes:
            raise ValueError("Tidak ada halaman dipilih.")
        indexes = sorted(set(indexes))
        for i in reversed(indexes):
            self.doc.delete_page(i)
        self._selection.clear()

    def insert_from(self, other_path, src_indexes, position):
        other = fitz.open(other_path)
        try:
            if position < 0 or position > self.page_count:
                raise ValueError("Posisi sisip tidak valid.")
            src_indexes = sorted(set(src_indexes))
            for i in src_indexes:
                if i < 0 or i >= other.page_count:
                    raise ValueError(f"Halaman {i+1} tidak ada di PDF sumber.")
            other.select(src_indexes)
            self.doc.insert_pdf(other, start_at=position)
        finally:
            other.close()
        self._selection.clear()

    def merge(self, paths):
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka. Buka/beri dokumen induk dulu.")
        for p in paths:
            src = fitz.open(p)
            try:
                self.doc.insert_pdf(src)
            finally:
                src.close()
        self._selection.clear()

    def split(self, start, end):
        if start < 0 or end >= self.page_count or start > end:
            raise ValueError("Rentang halaman tidak valid.")
        out = fitz.open()
        out.insert_pdf(self.doc, from_page=start, to_page=end)
        return out

    def split_all(self):
        out = fitz.open()
        out.insert_pdf(self.doc)
        return out

    def rotate(self, indexes, angle):
        if not indexes:
            raise ValueError("Tidak ada halaman dipilih.")
        for i in indexes:
            page = self.doc.load_page(i)
            page.set_rotation((page.rotation + angle) % 360)

    def move_pages(self, indexes, target_pos):
        if not indexes:
            raise ValueError("Tidak ada halaman dipilih.")
        indexes = sorted(set(indexes))
        count = len(indexes)
        total = self.page_count
        if target_pos < 0 or target_pos > total - count:
            raise ValueError("Posisi tujuan tidak valid.")
        remaining = [i for i in range(total) if i not in indexes]
        # sisipkan blok pilihan ke posisi target_pos di daftar remaining
        new_order = remaining[:target_pos] + indexes + remaining[target_pos:]
        self.doc.select(new_order)
        self._selection.clear()

    def add_watermark(self, text=None, image_path=None, opacity=0.3, angle=45):
        if not text and not image_path:
            raise ValueError("Isi teks watermark atau pilih gambar.")
        for pno in range(self.page_count):
            page = self.doc.load_page(pno)
            rect = page.rect
            if text:
                fs = max(24, rect.width / 12)
                cx, cy = rect.width / 2, rect.height / 2
                a = math.radians(angle)
                rot = fitz.Matrix(math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
                page.insert_text(
                    fitz.Point(cx, cy),
                    text,
                    fontsize=fs,
                    color=(0.5, 0.5, 0.5),
                    fill_opacity=opacity,
                    stroke_opacity=opacity,
                    morph=(fitz.Point(cx, cy), rot),
                    overlay=True,
                )
            elif image_path:
                if not os.path.exists(image_path):
                    raise ValueError("File gambar tidak ditemukan.")
                img = fitz.Pixmap(image_path)
                w = rect.width * 0.4
                h = w * img.height / img.width
                page.insert_image(
                    fitz.Rect((rect.width - w) / 2, (rect.height - h) / 2, (rect.width + w) / 2, (rect.height + h) / 2),
                    filename=image_path,
                    overlay=True,
                )
                img = None

    def _save_over_original(self, target, **save_kwargs):
        """Simpan dokumen dengan aman, termasuk menimpa file asli yang sedang terbuka.

        PyMuPDF melarang doc.save() ke path asal tanpa incremental=True (error
        "save to original must be incremental"). Trik: simpan ke file sementara
        di folder yang sama lalu replace secara atomik.
        """
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if target and os.path.abspath(target) == os.path.abspath(self.path or ""):
            tmp = target + ".tmp-save.pdf"
            self.doc.save(tmp, **save_kwargs)
            os.replace(tmp, target)
        else:
            self.doc.save(target or "_output.pdf", **save_kwargs)

    def protect(self, password, owner_password=None):
        if not password:
            raise ValueError("Password tidak boleh kosong.")
        owner = owner_password or password
        self._save_over_original(
            self.path or "_protected.pdf",
            encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner, user_pw=password,
        )

    def unlock(self, out_path=None):
        """Simpan salinan PDF tanpa proteksi (dokumen harus sudah ter-autentikasi)."""
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if not (self.was_encrypted or self.doc.is_encrypted or self.doc.needs_pass):
            raise ValueError("PDF ini tidak terenkripsi — tidak ada yang perlu dibuka.")
        target = out_path or (os.path.splitext(self.path)[0] + "_unlocked.pdf")
        # permissions=-1 & encryption NONE -> salinan bebas proteksi
        self._save_over_original(target, encryption=fitz.PDF_ENCRYPT_NONE, permissions=int(-1))
        return target

    def save_as(self, path):
        self.doc.save(path, garbage=3, deflate=True)

    def save_to_bytes(self):
        return self.doc.tobytes(garbage=3, deflate=True)

    # ---------- Fitur baru: Edit Teks ----------

    def get_text_spans(self, page_index):
        """Ambil daftar span teks pada halaman (untuk fitur edit teks).

        Setiap span: text, bbox, origin (baseline), font, size, color (sRGB int),
        alpha, flags, dir (arah baris).
        """
        page = self.doc.load_page(page_index)
        spans = []
        for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        continue
                    spans.append({
                        "text": text,
                        "bbox": fitz.Rect(span["bbox"]),
                        "origin": fitz.Point(span["origin"]),
                        "font": span["font"],
                        "size": span["size"],
                        "color": span.get("color", 0),
                        "alpha": span.get("alpha", 255),
                        "flags": span.get("flags", 0),
                        "dir": line.get("dir", (1.0, 0.0)),
                    })
        return spans

    def _page_font_ref(self, page, basefont):
        """Cari reference name ('/F1') font di page yang cocok dengan basefont."""
        for f in page.get_fonts(full=True):
            if f[3] == basefont:
                name = f[4]
                return name if name.startswith("/") else "/" + name
        return None

    def _font_from_page(self, page, refname):
        """Buat fitz.Font dari font yang sudah ada di page (untuk pengukuran lebar)."""
        xref = None
        for f in page.get_fonts(full=True):
            name = f[4]
            name = name if name.startswith("/") else "/" + name
            if name == refname:
                xref = f[0]
                break
        if xref is None:
            return fitz.Font(fontname="helv")
        try:
            _, _, _, buffer = self.doc.extract_font(xref)
            if buffer:
                return fitz.Font(fontbuffer=buffer)
        except Exception:
            pass
        return fitz.Font(fontname="helv")

    def _resolve_font(self, page, span, bold, italic, serif, mono, warnings):
        """Tentukan font untuk insert: font asli page > fontfile sistem > Base-14."""
        basefont = span["font"]
        refname = self._page_font_ref(page, basefont)
        if refname:
            return refname, None, basefont
        path = _find_system_font(bold=bold, italic=italic, serif=serif, mono=mono)
        if path:
            warnings.append(
                f"Font asli '{basefont}' tidak tersedia untuk penyisipan; fallback: {os.path.basename(path)}"
            )
            return "f0", path, os.path.basename(path)
        code = _base14_code(bold, italic, serif, mono)
        warnings.append(f"Font asli '{basefont}' tidak tersedia; fallback Base-14: {code}")
        return code, None, code

    def replace_text(
        self,
        page_index,
        span_index,
        new_text,
        auto_shrink=False,
        allow_longer=False,
        whiteout=True,
        custom_fontsize=None,
        custom_color=None,
    ):
        """Ganti teks satu span dengan teks baru, mempertahankan gaya asli.

        Presisi: font/size/color/baseline disalin dari span asli. Mendukung
        auto_shrink untuk menyesuaikan ukuran font otomatis jika teks lebih panjang,
        dan whiteout untuk membersihkan latar belakang secara menyeluruh.

        Returns dict: {ok, font_used, warnings, replaced}
        """
        page = self.doc.load_page(page_index)
        spans = self.get_text_spans(page_index)
        if span_index < 0 or span_index >= len(spans):
            raise ValueError("Pilihan teks tidak valid.")
        span = spans[span_index]
        old_text = span["text"]
        if not new_text:
            raise ValueError("Teks baru tidak boleh kosong.")
        if new_text == old_text:
            return {"ok": True, "font_used": span["font"], "warnings": [], "replaced": False}

        bbox = span["bbox"]
        origin = span["origin"]
        size = float(custom_fontsize) if custom_fontsize is not None else float(span["size"])
        color = custom_color if custom_color is not None else tuple(c / 255.0 for c in fitz.sRGB_to_rgb(span["color"]))
        alpha = span.get("alpha", 255)
        flags = span.get("flags", 0)
        bold = bool(flags & fitz.TEXT_FONT_BOLD)
        italic = bool(flags & fitz.TEXT_FONT_ITALIC)
        serif = bool(flags & fitz.TEXT_FONT_SERIFED)
        mono = bool(flags & fitz.TEXT_FONT_MONOSPACED)
        line_dir = span["dir"]

        warnings = []
        fontname, fontfile, font_used = self._resolve_font(page, span, bold, italic, serif, mono, warnings)

        # Ukur lebar teks baru dengan font terpilih
        try:
            if fontfile:
                probe = fitz.Font(fontfile=fontfile)
            elif fontname.startswith("/"):
                probe = self._font_from_page(page, fontname)
            else:
                probe = fitz.Font(fontname=fontname)
            new_len = probe.text_length(new_text, fontsize=size)
        except Exception:
            new_len = len(new_text) * size * 0.55

        if new_len > bbox.width + 1.0:
            if auto_shrink and new_len > 0:
                scale_factor = (bbox.width) / new_len
                size = max(5.0, size * scale_factor)
                warnings.append(
                    f"Ukuran font disesuaikan ({span['size']:.1f}pt \u2192 {size:.1f}pt) agar pas di dalam area."
                )
            elif not allow_longer:
                raise ValueError(
                    f"Teks baru terlalu panjang: {new_len:.1f}pt, area asli {bbox.width:.1f}pt.\n"
                    "Akan menimpa teks lain. Persingkat teks, aktifkan penyesuaian ukuran otomatis, atau edit per kata."
                )

        # Hapus teks asli dari stream dokumen
        page.add_redact_annot(bbox)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )

        if whiteout:
            page.draw_rect(bbox, fill=(1, 1, 1), color=None, overlay=True)

        # Sisipkan teks baru dengan properti asli
        kwargs = {
            "fontsize": size,
            "color": color,
            "fill_opacity": alpha / 255.0,
            "stroke_opacity": alpha / 255.0,
            "overlay": True,
        }
        if fontfile:
            kwargs["fontname"] = "f0"
            kwargs["fontfile"] = fontfile
        else:
            kwargs["fontname"] = fontname
        dx, dy = line_dir
        if abs(dx - 1.0) > 1e-3 or abs(dy) > 1e-3:
            kwargs["morph"] = (origin, fitz.Matrix(dx, dy, -dy, dx, 0, 0))
        page.insert_text(origin, new_text, **kwargs)

        return {"ok": True, "font_used": font_used, "warnings": warnings, "replaced": True}

    def replace_all_text(self, page_index, old_text, new_text, auto_shrink=True, whiteout=True):
        """Ganti semua span teks di halaman yang persis atau mengandung old_text.

        PENTING: setiap replace_text melakukan apply_redactions yang mengubah
        struktur span di halaman. Kalau kita memproses indeks berurutan ke bawah,
        indeks span setelah titik edit bergeser -> teks salah sasaran / hilang
        (bug korupsi data). Solusi: kumpulkan dulu semua indeks yang cocok pada
        snapshot awal, lalu proses dari indeks TERBESAR; penghapusan hanya
        menggeser span di belakangnya, jadi indeks yang lebih kecil tetap valid.
        """
        spans = self.get_text_spans(page_index)
        matched = [idx for idx, s in enumerate(spans) if old_text in s["text"]]
        count = 0
        all_warnings = []
        for idx in reversed(matched):
            updated = spans[idx]["text"].replace(old_text, new_text)
            res = self.replace_text(
                page_index, idx, updated, auto_shrink=auto_shrink, allow_longer=True, whiteout=whiteout
            )
            if res.get("replaced", True):
                count += 1
            all_warnings.extend(res.get("warnings", []))
        return {"ok": True, "count": count, "warnings": all_warnings}

    def add_custom_text(
        self,
        page_index,
        text,
        x,
        y,
        fontsize=12,
        font_family="helv",
        color=(0, 0, 0),
        bold=False,
        italic=False,
        whiteout=True,
        whiteout_padding=3,
        box_width=None,
        box_height=None,
    ):
        """Tambahkan teks bebas pada koordinat (x, y) halaman.

        Mendukung teks multi-baris dan opsi whiteout (latar belakang putih)
        untuk menimpa teks pada dokumen scan / gambar maupun digital.
        """
        page = self.doc.load_page(page_index)
        if not text:
            raise ValueError("Teks tidak boleh kosong.")

        font_code = _base14_code(
            bold=bold,
            italic=italic,
            serif=(font_family == "times"),
            mono=(font_family == "courier"),
        )

        lines = text.split("\n")
        line_height = fontsize * 1.25

        try:
            probe = fitz.Font(fontname=font_code)
            max_line_width = max(probe.text_length(line, fontsize=fontsize) for line in lines)
        except Exception:
            max_line_width = max(len(line) * fontsize * 0.6 for line in lines)

        total_height = len(lines) * line_height

        w = box_width if (box_width and box_width > 0) else (max_line_width + (whiteout_padding * 2))
        h = box_height if (box_height and box_height > 0) else (total_height + (whiteout_padding * 2))

        rect = fitz.Rect(
            x - whiteout_padding,
            y - whiteout_padding,
            x - whiteout_padding + w,
            y - whiteout_padding + h,
        )

        if whiteout:
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
            page.draw_rect(rect, fill=(1, 1, 1), color=None, overlay=True)

        for i, line in enumerate(lines):
            baseline_y = y + (i * line_height) + (fontsize * 0.85)
            origin = fitz.Point(x, baseline_y)
            page.insert_text(
                origin,
                line,
                fontsize=fontsize,
                fontname=font_code,
                color=color,
                overlay=True,
            )

        return {"ok": True, "rect": rect, "lines_count": len(lines)}

    def whiteout_area(self, page_index, rect):
        """Hapus / tutup area tertentu dengan warna putih solid."""
        page = self.doc.load_page(page_index)
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )
        page.draw_rect(rect, fill=(1, 1, 1), color=None, overlay=True)
        return {"ok": True}

    # ---------- Fitur baru: Compress & Nomor Halaman ----------

    def compress(self, path=None, images=True):
        """Kompres dokumen: garbage collection, deflate, subset font, rewrite image."""
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if images:
            try:
                self.doc.rewrite_images()
            except Exception:
                pass
        try:
            self.doc.subset_fonts()
        except Exception:
            pass
        target = path or self.path
        self._save_over_original(target, garbage=4, deflate=True)

    def add_page_numbers(self, start=1, position="bottom", fontsize=11, color=(0.45, 0.45, 0.45)):
        """Tambahkan nomor halaman pada setiap halaman."""
        for i in range(self.page_count):
            page = self.doc.load_page(i)
            rect = page.rect
            text = str(start + i)
            w = fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
            if position == "top":
                p = fitz.Point((rect.width - w) / 2, 24)
            elif position == "bottom-right":
                p = fitz.Point(rect.width - 36 - w, rect.height - 24)
            elif position == "bottom-left":
                p = fitz.Point(36, rect.height - 24)
            else:  # bottom center
                p = fitz.Point((rect.width - w) / 2, rect.height - 24)
            page.insert_text(p, text, fontsize=fontsize, color=color, overlay=True)

    # ---------- Fitur baru: Ekspor ke Image ----------

    def export_images(self, page_indices, out_dir, fmt="png", dpi=150, quality=90, progress_cb=None):
        """Ekspor halaman-halaman menjadi file gambar PNG/JPEG.

        Return daftar path file yang ditulis.
        """
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if not page_indices:
            raise ValueError("Tidak ada halaman yang dipilih.")
        fmt = (fmt or "png").lower()
        if fmt not in ("png", "jpeg", "jpg"):
            raise ValueError(f"Format tidak didukung: {fmt} (gunakan png/jpeg)")
        ext = "jpg" if fmt in ("jpeg", "jpg") else "png"
        for idx in page_indices:
            if not (0 <= idx < self.page_count):
                raise ValueError(f"Nomor halaman tidak valid: {idx + 1}")
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else "halaman"
        written = []
        total = len(page_indices)
        for i, idx in enumerate(page_indices):
            page = self.doc.load_page(idx)
            pix = page.get_pixmap(dpi=dpi)
            out_path = os.path.join(out_dir, f"{base}_halaman-{idx + 1}.{ext}")
            if ext == "jpg":
                pix.save(out_path, jpg_quality=quality)
            else:
                pix.save(out_path)
            pix = None
            written.append(out_path)
            if progress_cb:
                progress_cb(i + 1, total)
        return written

    # ---------- Fitur baru: Organisasi & Lain-lain ----------

    def reverse_pages(self):
        """Balik urutan semua halaman (terakhir jadi pertama)."""
        if not self.doc or self.page_count < 2:
            return
        self.doc.select(list(range(self.page_count - 1, -1, -1)))
        self._selection.clear()

    def crop_pages(self, indexes=None, left=0.05, top=0.05, right=0.05, bottom=0.05):
        """Potong tepi halaman (nilai rasio 0-0.4 dari lebar/tinggi halaman)."""
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        for name, val in (("kiri", left), ("atas", top), ("kanan", right), ("bawah", bottom)):
            if not (0 <= float(val) <= 0.45):
                raise ValueError(f"Rasio potong {name} harus 0 - 0.45.")
        targets = sorted(set(indexes)) if indexes else list(range(self.page_count))
        if not targets:
            raise ValueError("Tidak ada halaman dipilih.")
        for i in targets:
            if not (0 <= i < self.page_count):
                raise ValueError(f"Nomor halaman tidak valid: {i + 1}")
            page = self.doc.load_page(i)
            r = page.rect
            dx, dy = r.width * float(left), r.height * float(top)
            dx2, dy2 = r.width * float(right), r.height * float(bottom)
            box = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 - dx2, r.y1 - dy2)
            if box.is_empty or box.width < 20 or box.height < 20:
                raise ValueError(f"Hasil potong halaman {i + 1} terlalu kecil.")
            page.set_cropbox(box)

    def stamp_image(self, page_index, image_path, x=None, y=None, width_ratio=0.3):
        """Tempel gambar (tanda tangan / stempel) pada halaman.

        x, y = posisi pojok kiri-atas dalam pt. Default: kanan-bawah.
        """
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if not image_path or not os.path.exists(image_path):
            raise ValueError("File gambar tidak ditemukan.")
        page = self.doc.load_page(page_index)
        rect = page.rect
        try:
            img = fitz.Pixmap(image_path)
            ratio = img.height / img.width if img.width else 1.0
            img = None
        except Exception as e:
            raise ValueError(f"Gagal membaca gambar: {e}")
        w = rect.width * float(width_ratio)
        h = w * ratio
        if x is None:
            x = rect.width - w - 40
        if y is None:
            y = rect.height - h - 40
        x = max(0.0, min(rect.width - 10, float(x)))
        y = max(0.0, min(rect.height - 10, float(y)))
        target = fitz.Rect(x, y, min(x + w, rect.width), min(y + h, rect.height))
        page.insert_image(target, filename=image_path, overlay=True)
        return {"ok": True, "rect": target}

    def get_metadata(self):
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        meta = dict(self.doc.metadata or {})
        meta["_page_count"] = self.page_count
        meta["_encrypted"] = bool(self.doc.is_encrypted)
        if self.path:
            try:
                meta["_file_size"] = os.path.getsize(self.path)
            except OSError:
                meta["_file_size"] = None
        return meta

    def set_metadata(self, **fields):
        """Set metadata dokumen: title, author, subject, keywords, creator, producer."""
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        allowed = {"title", "author", "subject", "keywords", "creator", "producer"}
        meta = self.doc.metadata or {}
        for k, v in fields.items():
            if k in allowed:
                meta[k] = str(v) if v is not None else ""
        self.doc.set_metadata(meta)

    def extract_images(self, page_indices, out_dir, min_size=50, progress_cb=None):
        """Ekstrak gambar tertanam dari halaman menjadi file PNG.

        Return daftar path file yang ditulis. min_size = ukuran minimal px.
        """
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        if not os.path.isdir(out_dir):
            raise ValueError("Folder tujuan tidak ditemukan.")
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else "gambar"
        seen, written = set(), []
        total = len(page_indices)
        done = 0
        for pno in page_indices:
            page = self.doc.load_page(pno)
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(self.doc, xref)
                    if pix.width < min_size or pix.height < min_size:
                        pix = None
                        continue
                    if pix.colorspace and pix.colorspace.n > 3:  # CMYK dll -> RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.alpha:
                        pix = fitz.Pixmap(pix, 0)
                    out_path = os.path.join(out_dir, f"{base}_p{pno + 1}_{xref}.png")
                    pix.save(out_path)
                    written.append(out_path)
                    pix = None
                except Exception:
                    continue
            done += 1
            if progress_cb:
                progress_cb(done, total)
        return written

    def export_text(self, out_path, pages=None):
        """Ekspor teks seluruh / sebagian halaman menjadi file .txt."""
        if not self.doc:
            raise ValueError("Belum ada PDF yang dibuka.")
        targets = pages if pages is not None else range(self.page_count)
        chunks = []
        for i in targets:
            if not (0 <= i < self.page_count):
                continue
            chunks.append(f"===== Halaman {i + 1} =====\n")
            chunks.append(self.doc.load_page(i).get_text("text").strip())
            chunks.append("\n\n")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(chunks))
        return out_path


# ---------- Fungsi mandiri (tidak butuh objek PDFTool) ----------

def repair_pdf(src_path, dst_path):
    """Perbaiki PDF rusak: buka dengan mode recovery MuPDF lalu simpan bersih.

    Return (ok: bool, pesan: str).
    """
    doc = None
    try:
        try:
            doc = fitz.open(src_path)
        except Exception:
            # Coba baca byte kasar; MuPDF bisa memulihkan struktur xref yang rusak.
            with open(src_path, "rb") as f:
                raw = f.read()
            doc = fitz.open(stream=raw, filetype="pdf")
        n = doc.page_count
        doc.save(dst_path, garbage=4, deflate=True)
        return True, f"Berhasil diperbaiki ({n} halaman): {dst_path}"
    except Exception as e:
        return False, f"Gagal memperbaiki: {e}"
    finally:
        if doc:
            try:
                doc.close()
            except Exception:
                pass