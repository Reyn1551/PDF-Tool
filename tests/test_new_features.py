#!/usr/bin/env python3
"""Bug-regression + new-feature tests for PDF Tool.

Covers:
A. 4 fatal bug regressions : compress/protect to original path, merge without a
                              document, replace_all_text data corruption.
B. New backend features    : unlock, reverse, crop, stamp, metadata,
                             extract_images, export_text, repair_pdf.
C. Conversion module       : images->pdf, office->pdf (LibreOffice),
                             pdf->docx, html->pdf.

Run:  python3 tests/test_new_features.py
Exit code 0 = all checks passed.
"""
import os
import shutil
import sys
import tempfile

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_ops import PDFTool, repair_pdf
from pdf_convert import images_to_pdf, pdf_to_docx, html_to_pdf, office_to_pdf, find_soffice

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


def make_pdf(path, n_pages=1, text=None):
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        t = text if text is not None else f"Page {i + 1}"
        page.insert_text((72, 72), f"{t} {i + 1}" if text is not None else t, fontsize=14)
    doc.save(path)
    doc.close()
    return path


def main():
    d = tempfile.mkdtemp(prefix="pdftool_test_")
    try:
        _run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n===== RESULT: {PASS} PASS, {FAIL} FAIL =====")
    return 0 if FAIL == 0 else 1


def _run(d):
    # ---------- A. FATAL BUG REGRESSIONS ----------
    print("== A1. Compress overwriting the original file ('save to original' bug) ==")
    p = make_pdf(os.path.join(d, "a.pdf"))
    t = PDFTool(); t.open(p)
    try:
        t.compress()  # default path == self.path
        check("compress to original path succeeds", os.path.getsize(p) > 0)
    except Exception as e:
        check("compress to original path succeeds", False, str(e))

    print("== A2. Merge without an open document ==")
    t2 = PDFTool()
    try:
        t2.merge([p])
        check("merge without doc -> clear ValueError", False, "nothing was raised")
    except ValueError:
        check("merge without doc -> clear ValueError", True)
    except Exception as e:
        check("merge without doc -> clear ValueError", False, f"{type(e).__name__}: {e}")

    print("== A3. Protect overwriting the original file ==")
    p2 = make_pdf(os.path.join(d, "b.pdf"))
    t3 = PDFTool(); t3.open(p2)
    try:
        t3.protect("secret123")
        check("protect to original path succeeds", True)
        t3b = PDFTool()
        try:
            t3b.open(p2)  # must require the password now
            check("protected output truly encrypted", False, "opened without password")
        except ValueError as e:
            check("protected output truly encrypted", str(e) == "NEEDS_PASSWORD")
    except Exception as e:
        check("protect to original path succeeds", False, str(e))

    print("== A4. replace_all_text no longer corrupts other text ==")
    doc = fitz.open(); pg = doc.new_page()
    pg.insert_text((72, 100), "TODO first line", fontsize=14)
    pg.insert_text((72, 130), "TODO second line", fontsize=14)
    pg.insert_text((72, 160), "keep me", fontsize=14)
    q = os.path.join(d, "c.pdf"); doc.save(q); doc.close()
    t4 = PDFTool(); t4.open(q)
    r = t4.replace_all_text(0, "TODO", "DONE")
    txt = t4.doc.load_page(0).get_text()
    check("all occurrences replaced", r["count"] == 2 and txt.count("DONE") == 2, repr(txt))
    check("other text intact ('keep me')", "keep me" in txt, repr(txt))
    check("no TODO remains", "TODO" not in txt, repr(txt))

    # ---------- B. NEW BACKEND FEATURES ----------
    print("== B1. Unlock PDF ==")
    enc = os.path.join(d, "enc.pdf")
    doc = fitz.open(); pg = doc.new_page(); pg.insert_text((72, 72), "secret", fontsize=14)
    doc.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="key", user_pw="key")
    doc.close()
    t5 = PDFTool(); t5.open(enc, password="key")
    out_unlock = t5.unlock(os.path.join(d, "unlocked.pdf"))
    t6 = PDFTool(); t6.open(out_unlock)  # must open without any password
    check("unlocked copy opens freely", t6.doc.load_page(0).get_text().strip() == "secret")

    print("== B2. Reverse page order ==")
    p3 = make_pdf(os.path.join(d, "rev.pdf"), n_pages=3)
    t7 = PDFTool(); t7.open(p3); t7.reverse_pages()
    check("first page is now 'Page 3'", t7.doc.load_page(0).get_text().find("3") >= 0)

    print("== B3. Crop page edges ==")
    t7.crop_pages([0], left=0.1, top=0.05, right=0.1, bottom=0.05)
    cb = t7.doc.load_page(0).cropbox
    check(f"width trimmed to ~80% ({cb.width:.0f})", abs(cb.width - 595 * 0.8) < 2)

    print("== B4. Image stamp ==")
    from PIL import Image
    img_path = os.path.join(d, "stamp.png")
    Image.new("RGB", (120, 60), (30, 30, 200)).save(img_path)
    res = t7.stamp_image(0, img_path)
    check("stamp applied", res["ok"] and res["rect"].width > 0)

    print("== B5. Metadata ==")
    t7.set_metadata(title="Test Title", author="Tester")
    m = t7.get_metadata()
    check("metadata stored", m["title"] == "Test Title" and m["author"] == "Tester")

    print("== B6. Extract images ==")
    pimg = os.path.join(d, "with_img.pdf")
    doc = fitz.open(); pg = doc.new_page()
    pg.insert_image(fitz.Rect(50, 50, 250, 250), filename=img_path)
    doc.save(pimg); doc.close()
    t8 = PDFTool(); t8.open(pimg)
    got = t8.extract_images([0], d)
    check("1 image extracted", len(got) >= 1, f"got {len(got)}")
    if got:
        check("extracted file is a valid PNG",
              open(got[0], "rb").read(8) == b"\x89PNG\r\n\x1a\n")

    print("== B7. Export text ==")
    tx = t8.export_text(os.path.join(d, "out.txt"))
    body = open(tx, encoding="utf-8").read()
    check("contains page header", "Page 1" in body or "===== Page 1 =====" in body, repr(body[:80]))

    print("== B8. Repair broken PDF ==")
    broken = os.path.join(d, "broken.pdf")
    raw = open(pimg, "rb").read()
    cut = raw.rfind(b"startxref")
    with open(broken, "wb") as f:  # truncate the xref table -> classic corruption
        f.write(raw[:cut])
    ok, msg = repair_pdf(broken, os.path.join(d, "repaired.pdf"))
    check("broken PDF successfully recovered", ok and fitz.open(os.path.join(d, "repaired.pdf")).page_count == 1)
    ok2, _ = repair_pdf(os.path.join(d, "missing.pdf"), os.path.join(d, "x.pdf"))
    check("missing file -> clean failure (False)", ok2 is False)

    # ---------- C. CONVERSION MODULE ----------
    print("== C1. Image(s) -> PDF ==")
    im1 = os.path.join(d, "i1.png"); im2 = os.path.join(d, "i2.jpg")
    Image.new("RGB", (400, 300), (200, 40, 40)).save(im1)
    Image.new("RGB", (300, 500), (40, 200, 40)).save(im2, quality=85)
    out_auto = images_to_pdf([im1, im2], os.path.join(d, "auto.pdf"), page_mode="auto")
    da = fitz.open(out_auto)
    check("auto mode: 2 pages", da.page_count == 2)
    check("auto mode size follows image", abs(da.load_page(0).rect.width - 448) < 2)  # 400+2*24
    out_a4 = images_to_pdf([im1, im2], os.path.join(d, "a4.pdf"), page_mode="a4")
    da4 = fitz.open(out_a4)
    # Pages follow input order: pg0 = im1 (400x300 landscape),
    # pg1 = im2 (300x500 portrait). Auto-orientation adapts A4.
    pg0 = da4.load_page(0)
    check("A4 auto-landscape for wide image (842x595)",
          abs(pg0.rect.width - 842) < 1 and abs(pg0.rect.height - 595) < 1,
          f"got {pg0.rect.width}x{pg0.rect.height}")
    pg1 = da4.load_page(1)
    check("A4 auto-portrait for tall image (595x842)",
          abs(pg1.rect.width - 595) < 1 and abs(pg1.rect.height - 842) < 1,
          f"got {pg1.rect.width}x{pg1.rect.height}")

    print("== C2. PDF -> Word ==")
    src_txt = make_pdf(os.path.join(d, "word_src.pdf"), n_pages=1, text="Conversion test sentence")
    pdf_to_docx(src_txt, os.path.join(d, "out.docx"))
    import docx as docx_mod
    paras = [pp.text for pp in docx_mod.Document(os.path.join(d, "out.docx")).paragraphs]
    joined = "\n".join(paras)
    check("docx contains source text", "Conversion test sentence" in joined or "conversion test" in joined.lower(), repr(paras[:6]))

    print("== C3. HTML -> PDF ==")
    hout = html_to_pdf("<h1>Test Title</h1><p>A <b>bold</b> paragraph.</p>", os.path.join(d, "web.pdf"))
    dh = fitz.open(hout)
    check("html->pdf single page contains text", dh.page_count == 1 and "Test Title" in dh.load_page(0).get_text())

    if find_soffice():
        print("== C4. Office -> PDF (LibreOffice) ==")
        rtf = os.path.join(d, "test.rtf")
        with open(rtf, "w") as f:
            f.write(r"{\rtf1\ansi Hello from RTF\par}")
        try:
            opdf = office_to_pdf(rtf, d)
            txt = fitz.open(opdf).load_page(0).get_text()
            check("rtf converted & text readable", "Hello" in txt, repr(txt[:50]))
        except ValueError as e:
            check("rtf converted & text readable", False, str(e))
    else:
        print("== C4. Office -> PDF skipped (LibreOffice not installed) ==")


if __name__ == "__main__":
    sys.exit(main())
