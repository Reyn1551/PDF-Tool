"""Modul konversi format untuk PDF Tool.

Fitur gaya iLovePDF yang butuh alat eksternal / library tambahan:
- Image(s) -> PDF            : murni PyMuPDF
- Office -> PDF              : LibreOffice headless (soffice) bila terpasang
- PDF -> Word (.docx)        : python-docx (best-effort; layout sederhana)
- HTML -> PDF                : PyMuPDF Story (fallback ke LibreOffice)

Semua fungsi melempar ValueError dengan pesan ramah pengguna.
"""
import os
import shutil
import subprocess

import fitz


OFFICE_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".csv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp"}


def _require_file(path):
    if not path or not os.path.isfile(path):
        raise ValueError(f"File tidak ditemukan: {path}")


# ---------------------------------------------------------------- Images -> PDF

def images_to_pdf(image_paths, out_path, page_mode="auto", orientation="auto",
                  margin_pt=24, quality=None):
    """Gabungkan gambar-gambar menjadi satu PDF.

    page_mode: "auto" (halaman mengikuti ukuran gambar + margin) atau "a4".
    orientation: "auto" | "portrait" | "landscape" (untuk mode a4).
    """
    if not image_paths:
        raise ValueError("Tidak ada gambar dipilih.")
    for p in image_paths:
        _require_file(p)
        if os.path.splitext(p)[1].lower() not in IMAGE_EXTS:
            raise ValueError(f"Bukan file gambar yang didukung: {os.path.basename(p)}")
    margin_pt = max(0, float(margin_pt))
    out = fitz.open()
    try:
        for p in image_paths:
            try:
                img = fitz.Pixmap(p)
                iw, ih = img.width, img.height
                img = None
            except Exception as e:
                raise ValueError(f"Gagal membaca gambar {os.path.basename(p)}: {e}")
            if page_mode == "a4":
                pw, ph = 595, 842
                if orientation == "landscape" or (orientation == "auto" and iw > ih):
                    pw, ph = ph, pw
                avail_w, avail_h = pw - 2 * margin_pt, ph - 2 * margin_pt
                scale = min(avail_w / iw, avail_h / ih)
                w, h = iw * scale, ih * scale
                page = out.new_page(width=pw, height=ph)
                rect = fitz.Rect((pw - w) / 2, (ph - h) / 2, (pw + w) / 2, (ph + h) / 2)
            else:
                page = out.new_page(width=iw + 2 * margin_pt, height=ih + 2 * margin_pt)
                rect = fitz.Rect(margin_pt, margin_pt, margin_pt + iw, margin_pt + ih)
            kwargs = {"filename": p}
            if quality is not None and os.path.splitext(p)[1].lower() in (".jpg", ".jpeg"):
                pass  # PyMuPDF menyimpan JPEG apa adanya saat insert via filename
            page.insert_image(rect, **kwargs)
        if out.page_count == 0:
            raise ValueError("Tidak ada halaman yang bisa dibuat.")
        out.save(out_path, garbage=3, deflate=True)
    finally:
        out.close()
    return out_path


# ---------------------------------------------------------------- Office -> PDF

def find_soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def office_to_pdf(src_path, out_dir):
    """Konversi dokumen Office (docx/xlsx/pptx/dll) menjadi PDF via LibreOffice.

    Return path PDF hasil. Melempar ValueError jika LibreOffice tidak ada
    atau konversi gagal.
    """
    _require_file(src_path)
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in OFFICE_EXTS:
        raise ValueError(
            f"Format tidak didukung: {ext}. Didukung: {', '.join(sorted(OFFICE_EXTS))}"
        )
    soffice = find_soffice()
    if not soffice:
        raise ValueError(
            "LibreOffice tidak ditemukan.\n"
            "Pasang dulu:  sudo apt install libreoffice"
        )
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        soffice, "--headless", "--norestore", "--nolockcheck",
        "--convert-to", "pdf", "--outdir", out_dir, src_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise ValueError("Konversi timeout (>3 menit). Coba file yang lebih kecil.")
    except FileNotFoundError:
        raise ValueError("LibreOffice tidak bisa dijalankan.")
    expected = os.path.join(out_dir, os.path.splitext(os.path.basename(src_path))[0] + ".pdf")
    if proc.returncode != 0 or not os.path.isfile(expected):
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = detail[-1] if detail else "tidak ada detail"
        raise ValueError(f"Konversi gagal: {detail}")
    return expected


# ---------------------------------------------------------------- PDF -> Word

def pdf_to_docx(pdf_path, docx_path, progress_cb=None):
    """Konversi PDF -> .docx best-effort (teks per paragraf per halaman).

    Layout rumit (kolom, tabel kompleks) tidak dijamin identik — hasil fokus
    pada teks yang dapat diedit. Return jumlah halaman yang dikonversi.
    """
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        raise ValueError(
            "Library python-docx belum terpasang.\n"
            "Pasang dengan:  pip3 install --user python-docx"
        )
    _require_file(pdf_path)
    doc_src = fitz.open(pdf_path)
    try:
        doc_out = Document()
        style = doc_out.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        total = doc_src.page_count
        for pno in range(total):
            page = doc_src.load_page(pno)
            doc_out.add_heading(f"Halaman {pno + 1}", level=3)
            blocks = page.get_text("blocks") or []
            for b in blocks:
                text = (b[4] or "").strip()
                if not text:
                    continue
                for para in text.splitlines():
                    para = para.strip()
                    if para:
                        doc_out.add_paragraph(para)
            if progress_cb:
                progress_cb(pno + 1, total)
        doc_out.save(docx_path)
        return total
    finally:
        doc_src.close()


# ---------------------------------------------------------------- HTML -> PDF

def html_to_pdf(html_text_or_path, out_path, is_file=False):
    """Render HTML sederhana menjadi PDF via PyMuPDF Story.

    Mendukung tag dasar (h1-h6, p, b, i, ul/ol/li, tabel sederhana, CSS inline).
    """
    if is_file:
        _require_file(html_text_or_path)
        with open(html_text_or_path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        html = html_text_or_path
    if not html or not html.strip():
        raise ValueError("HTML kosong.")
    try:
        story = fitz.Story(html=html)
    except AttributeError:
        raise ValueError("PyMuPDF versi ini tidak mendukung Story (HTML). Perbarui pymupdf.")
    except Exception as e:
        raise ValueError(f"HTML tidak valid: {e}")
    try:
        writer = fitz.DocumentWriter(out_path)
        mediabox = fitz.paper_rect("a4")
        where = mediabox + (36, 36, -36, -36)
        more = True
        while more:
            dev = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
        writer.close()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Gagal membuat PDF dari HTML: {e}")
    return out_path