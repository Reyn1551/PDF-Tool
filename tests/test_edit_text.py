#!/usr/bin/env python3
"""Test kuantitatif fitur Edit Teks + Compress + Nomor Halaman (PDF Tool).

Membuat PDF contoh dengan berbagai gaya teks, mengedit typo, lalu memverifikasi:
1. Font/size/color teks baru IDENTIK dengan asli
2. Tidak ada overlap (lebar teks baru <= area asli)
3. Baseline (posisi) sama
4. Teks lain tidak berubah
5. Teks terlalu panjang ditolak (cegah overlap)
6. Fallback font bekerja (font tidak embed / tidak ada di sistem)
7. Compress & Nomor Halaman berfungsi
8. Verifikasi fix preview (PPM header) + get_text_spans end-to-end

Jalankan:  python3 tests/test_edit_text.py
Exit code 0 = semua lolos.
"""
import os
import sys
import tempfile
import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_ops import PDFTool, _base14_code, _find_system_font

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def make_sample(path):
    """Buat PDF contoh: teks biasa, bold, italic, berwarna, multi-baris, kolom."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text(fitz.Point(72, 100), "Ini kalimat dengan typo: tehnis.",
                     fontsize=12, fontname="helv", color=(0, 0, 0))
    page.insert_text(fitz.Point(72, 130), "Bagian penting: laporan.",
                     fontsize=14, fontname="hebo", color=(0.2, 0.2, 0.8))
    page.insert_text(fitz.Point(72, 160), "Catatan kaki: revisi.",
                     fontsize=11, fontname="tiit", color=(0.8, 0.1, 0.1))
    page.insert_text(fitz.Point(72, 200), "Baris pertama dokumen.", fontsize=12, fontname="helv")
    page.insert_text(fitz.Point(72, 220), "Baris kedua dokumen.", fontsize=12, fontname="helv")
    page.insert_text(fitz.Point(72, 300), "Kolom kiri: data A.", fontsize=10, fontname="helv")
    page.insert_text(fitz.Point(350, 300), "Kolom kanan: data B.", fontsize=10, fontname="helv")
    doc.save(path)
    doc.close()


def find_span(tool, page_idx, needle):
    for i, s in enumerate(tool.get_text_spans(page_idx)):
        if needle in s["text"]:
            return i, s
    return None, None


def main():
    tmp = tempfile.mkdtemp(prefix="pdf_tool_test_")
    src = os.path.join(tmp, "sample.pdf")
    out = os.path.join(tmp, "edited.pdf")
    make_sample(src)

    print("== 1. Persiapan ==")
    tool = PDFTool()
    tool.open(src)
    spans = tool.get_text_spans(0)
    print(f"  Span terdeteksi: {len(spans)}")
    check("PDF contoh terbuka", tool.page_count == 1)
    check("Span teks terdeteksi (>=6)", len(spans) >= 6)

    print("== 2. Edit typo (teks biasa, helv) ==")
    idx, s = find_span(tool, 0, "tehnis")
    check("Span typo ditemukan", idx is not None)
    if idx is not None:
        old_font, old_size, old_color = s["font"], s["size"], s["color"]
        old_bbox = s["bbox"]
        res = tool.replace_text(0, idx, "Ini kalimat dengan typo: teknis.")
        check("replace_text ok", res["ok"] and res["replaced"])
        check("Font asli dipakai (tanpa fallback)", res["font_used"] == old_font and not res["warnings"])
        spans2 = tool.get_text_spans(0)
        new_s = None
        for sp in spans2:
            if "teknis" in sp["text"]:
                new_s = sp
                break
        check("Teks baru ada", new_s is not None)
        if new_s:
            check("Font identik", new_s["font"] == old_font, f"{new_s['font']} vs {old_font}")
            check("Size identik", abs(new_s["size"] - old_size) < 0.01, f"{new_s['size']} vs {old_size}")
            check("Color identik", new_s["color"] == old_color, f"#{new_s['color']:06x} vs #{old_color:06x}")
            check("Tidak overlap (lebar baru <= area asli)",
                  new_s["bbox"].width <= old_bbox.width + 1.0,
                  f"{new_s['bbox'].width:.1f} vs {old_bbox.width:.1f}")
            check("Baseline sama", abs(new_s["origin"].y - s["origin"].y) < 0.5,
                  f"{new_s['origin'].y:.2f} vs {s['origin'].y:.2f}")

    print("== 3. Edit teks bold berwarna (hebo) ==")
    idx, s = find_span(tool, 0, "laporan")
    if idx is not None:
        old_font, old_size, old_color = s["font"], s["size"], s["color"]
        res = tool.replace_text(0, idx, "Bagian penting: laporan!")
        check("replace bold ok", res["ok"] and res["replaced"])
        spans2 = tool.get_text_spans(0)
        new_s = None
        for sp in spans2:
            if "laporan!" in sp["text"]:
                new_s = sp
                break
        check("Teks bold baru ada", new_s is not None)
        if new_s:
            check("Font bold identik", new_s["font"] == old_font, f"{new_s['font']} vs {old_font}")
            check("Size bold identik", abs(new_s["size"] - old_size) < 0.01)
            check("Color bold identik", new_s["color"] == old_color, f"#{new_s['color']:06x} vs #{old_color:06x}")

    print("== 4. Teks lain tidak berubah ==")
    spans3 = tool.get_text_spans(0)
    texts = [sp["text"] for sp in spans3]
    for keep in ["Baris pertama dokumen.", "Baris kedua dokumen.",
                 "Kolom kiri: data A.", "Kolom kanan: data B.", "Catatan kaki: revisi."]:
        check(f"Teks lain utuh: '{keep[:24]}...'", any(keep in t for t in texts))

    print("== 5. Tolak teks terlalu panjang (cegah overlap) ==")
    idx, s = find_span(tool, 0, "Kolom kiri")
    if idx is not None:
        try:
            tool.replace_text(0, idx, "Kolom kiri dengan teks yang sangat panjang sekali melebihi area asli")
            check("Teks panjang ditolak", False, "seharusnya raise ValueError")
        except ValueError:
            check("Teks panjang ditolak (ValueError)", True)

    print("== 6. Simpan & buka ulang ==")
    tool.save_as(out)
    check("File hasil tersimpan", os.path.exists(out))
    tool2 = PDFTool()
    tool2.open(out)
    txt = tool2.doc[0].get_text()
    check("Teks hasil edit terbaca", "teknis" in txt)
    check("Typo asli hilang", "tehnis" not in txt)
    check("Teks lain utuh (buka ulang)", "Baris kedua dokumen." in txt and "Kolom kanan: data B." in txt)
    tool2.close()

    print("== 7. Fallback font (unit) ==")
    check("Base-14: sans bold = hebo", _base14_code(bold=True) == "hebo")
    check("Base-14: sans italic = heit", _base14_code(italic=True) == "heit")
    check("Base-14: serif bold-italic = tibi", _base14_code(bold=True, italic=True, serif=True) == "tibi")
    check("Base-14: mono = cour", _base14_code(mono=True) == "cour")
    sys_font = _find_system_font()
    check("Font sistem ditemukan (DejaVu/Liberation)", sys_font is not None, f"path={sys_font}")

    print("== 8. Compress ==")
    comp = os.path.join(tmp, "compressed.pdf")
    before = os.path.getsize(out)
    tool.compress(comp)
    after = os.path.getsize(comp)
    check("File terkompresi tersimpan", os.path.exists(comp))
    check("Ukuran tidak membengkak", after <= before * 1.5, f"{before} -> {after}")
    tool3 = PDFTool()
    tool3.open(comp)
    check("PDF terkompresi valid & teks utuh", "teknis" in tool3.doc[0].get_text())
    tool3.close()

    print("== 9. Nomor Halaman ==")
    tool.add_page_numbers(start=1, position="bottom")
    txt = tool.doc[0].get_text()
    check("Nomor halaman '1' ada", "1" in txt)
    tool.close()

    print("== 10. Verifikasi fix preview (PPM, bukan PNG) ==")
    # Root cause bug GUI: tk.PhotoImage(data=thumb.tobytes("png")) gagal karena
    # PhotoImage butuh base64/PPM, bukan raw PNG bytes. Fix memakai PPM.
    p0 = PDFTool()
    p0.open(out)
    pix = p0.get_thumb(0, max_width=64)
    ppm = pix.tobytes("ppm")
    check("PPM diawali header 'P6'", ppm[:2] == b"P6",
          f"got {ppm[:2]!r}")
    check("PPM menyertakan dimensi", b"P6\n%d %d\n" % (pix.width, pix.height) in ppm[:64],
          f"{ppm[:64]!r}")
    p0.close()

    # get_text_spans mengembalikan teks yang bisa dicari (bukan kosong)
    t = PDFTool()
    t.open(out)
    spans = t.get_text_spans(0)
    check("get_text_spans tidak kosong", len(spans) > 0, f"got {len(spans)}")
    check("Span berisi teks hasil edit", any("teknis" in s["text"] for s in spans))
    t.close()

    print("== 11. Auto-shrink pada replace_text ==")
    t_shrink = PDFTool()
    t_shrink.open(src)
    idx, s = find_span(t_shrink, 0, "Kolom kiri")
    check("Span Kolom kiri ditemukan", idx is not None)
    if idx is not None:
        res_shrink = t_shrink.replace_text(0, idx, "Kolom kiri dengan teks yang sangat panjang sekali", auto_shrink=True)
        check("replace_text auto_shrink berhasil tanpa crash", res_shrink["ok"] and res_shrink["replaced"])
        check("Ada warning penyesuaian font", any("disesuaikan" in w for w in res_shrink.get("warnings", [])))
        spans_shrunk = t_shrink.get_text_spans(0)
        found_shrunk = any("Kolom kiri dengan teks yang sangat panjang" in sp["text"] for sp in spans_shrunk)
        check("Teks baru auto-shrink tersimpan di dokumen", found_shrunk)
    t_shrink.close()

    print("== 12. Ganti Semua (replace_all_text) ==")
    t_all = PDFTool()
    t_all.open(src)
    res_all = t_all.replace_all_text(0, "dokumen", "berkas")
    check("replace_all_text berhasil", res_all["ok"] and res_all["count"] >= 2, f"count={res_all.get('count')}")
    txt_all = t_all.doc[0].get_text()
    check("Kata diganti di semua baris", "Baris pertama berkas." in txt_all and "Baris kedua berkas." in txt_all)
    t_all.close()

    print("== 13. Tambah Teks Bebas & Whiteout (add_custom_text & whiteout_area) ==")
    t_custom = PDFTool()
    t_custom.open(src)
    res_add = t_custom.add_custom_text(0, "Catatan Baru Ditambahkan\nBaris Kedua Catatan", x=100, y=400, fontsize=12, bold=True, whiteout=True)
    check("add_custom_text berhasil", res_add["ok"] and res_add["lines_count"] == 2)
    txt_custom = t_custom.doc[0].get_text()
    check("Teks baru ada di stream PDF", "Catatan Baru Ditambahkan" in txt_custom and "Baris Kedua Catatan" in txt_custom)

    res_whiteout = t_custom.whiteout_area(0, fitz.Rect(70, 48, 250, 110))
    check("whiteout_area berhasil", res_whiteout["ok"])
    txt_after_whiteout = t_custom.doc[0].get_text()
    check("Teks dalam area whiteout terhapus", "tehnis" not in txt_after_whiteout)
    t_custom.close()

    print("== 14. Dukungan Dokumen Scan / Gambar (Image PDF) ==")
    scan_doc = fitz.open()
    scan_page = scan_doc.new_page(width=595, height=842)
    # Tambahkan elemen gambar/grafis tanpa teks digital
    scan_page.draw_rect(fitz.Rect(50, 50, 500, 700), fill=(0.95, 0.95, 0.95), color=(0.8, 0.8, 0.8))
    scan_path = os.path.join(tmp, "scan_sample.pdf")
    scan_doc.save(scan_path)
    scan_doc.close()

    t_scan = PDFTool()
    t_scan.open(scan_path)
    scan_spans = t_scan.get_text_spans(0)
    check("PDF scan terdeteksi 0 span digital", len(scan_spans) == 0)
    # Tambahkan teks dengan whiteout di atas dokumen scan
    res_scan_add = t_scan.add_custom_text(0, "LEMBAR PENGESAHAN DOKUMEN SCAN", x=150, y=150, fontsize=14, bold=True, whiteout=True)
    check("add_custom_text pada scan PDF berhasil", res_scan_add["ok"])
    scan_txt = t_scan.doc[0].get_text()
    check("Teks baru terbaca pada scan PDF", "LEMBAR PENGESAHAN DOKUMEN SCAN" in scan_txt)
    t_scan.close()

    print(f"\n===== HASIL: {PASS} PASS, {FAIL} FAIL =====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()