#!/usr/bin/env python3
"""Quantitative tests for the Export PDF to Image feature (PNG/JPEG).

Verifies:
1. Exporting all pages to PNG -> correct count & filenames
2. Pixel dimensions proportional to DPI (96 vs 300)
3. Exporting a page subset -> only those pages are written
4. Valid JPEG (magic bytes, reopenable) and smaller than PNG for gradient content
5. Error cases: closed document, empty indices, out-of-range indices

Run:  python3 tests/test_pdf_to_image.py
Exit code 0 = all checks passed.
"""
import os
import sys
import tempfile
import zlib
import struct

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_ops import PDFTool

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


def _write_png(path, w, h, getpx):
    """Write a simple 8-bit RGB PNG (no external dependencies)."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(getpx(x, y))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat)
            + chunk(b"IEND", b"")
        )


def make_sample(path):
    """4-page A5 PDF with number markers; page 4 contains a color gradient."""
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=420, height=595)  # A5
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=18)
    # Smooth 2D gradient fills page 4 so JPEG looks smaller than PNG
    page = doc.load_page(3)
    W, H = 210, 297
    grad = os.path.join(os.path.dirname(path), "_grad.png")
    _write_png(
        grad,
        W,
        H,
        lambda x, y: (
            int(x / (W - 1) * 255),
            int(y / (H - 1) * 255),
            int((x * y / ((W - 1) * (H - 1)) * 3 % 1) * 255),
        ),
    )
    page.insert_image(page.rect, filename=grad)
    doc.save(path)
    doc.close()


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2img_test_")
    pdf_path = os.path.join(tmp, "sample.pdf")
    make_sample(pdf_path)

    tool = PDFTool()
    tool.open(pdf_path)

    outdir = os.path.join(tmp, "out_png")
    os.makedirs(outdir)

    print("== 1. Export all pages to PNG ==")
    written = tool.export_images([0, 1, 2, 3], outdir, fmt="png", dpi=96)
    check("file count = 4", len(written) == 4, f"got {len(written)}")
    names = sorted(os.listdir(outdir))
    expect_names = [
        "sample_page-1.png", "sample_page-2.png",
        "sample_page-3.png", "sample_page-4.png",
    ]
    check("filenames correct", names == expect_names, f"got {names}")

    print("== 2. Pixel dimensions match DPI ==")
    page_w = 420.0  # pt width of the A5 sample page
    pix96 = fitz.Pixmap(written[0])
    exp96 = round(page_w * 96 / 72)
    check(
        "width @96dpi ~560px",
        abs(pix96.width - exp96) <= 1,
        f"got {pix96.width}, expect ~{exp96}",
    )
    outdir300 = os.path.join(tmp, "out_300")
    os.makedirs(outdir300)
    written300 = tool.export_images([0], outdir300, fmt="png", dpi=300)
    pix300 = fitz.Pixmap(written300[0])
    exp300 = round(page_w * 300 / 72)
    check(
        "width @300dpi ~1750px",
        abs(pix300.width - exp300) <= 1,
        f"got {pix300.width}, expect ~{exp300}",
    )
    ratio = pix300.width / max(1, pix96.width)
    check("ratio 300/96 ~3.125", abs(ratio - 300 / 96) < 0.05, f"got {ratio:.3f}")

    print("== 3. Export page subset ==")
    outsub = os.path.join(tmp, "out_sub")
    os.makedirs(outsub)
    written_sub = tool.export_images([0, 2], outsub, fmt="png", dpi=96)
    subnames = sorted(os.listdir(outsub))
    check("subset = 2 files", len(subnames) == 2 and len(written_sub) == 2, f"got {subnames}")
    check(
        "only pages 1 & 3",
        subnames == ["sample_page-1.png", "sample_page-3.png"],
        f"got {subnames}",
    )

    print("== 4. JPEG valid & smaller ==")
    outjpg = os.path.join(tmp, "out_jpg")
    os.makedirs(outjpg)
    written_jpg = tool.export_images([3], outjpg, fmt="jpeg", dpi=150, quality=80)
    jpg_path = written_jpg[0]
    with open(jpg_path, "rb") as f:
        head = f.read(3)
    check("JPEG magic bytes", head[:2] == b"\xff\xd8", f"got {head!r}")
    reopened = None
    try:
        reopened = fitz.Pixmap(jpg_path)
        ok_open = True
    except Exception as e:
        ok_open = False
        print(f"    reopen error: {e}")
    check("JPEG reopenable by PyMuPDF", ok_open)
    png_counterpart = os.path.join(
        tmp, "out_cmp"
    )
    os.makedirs(png_counterpart)
    tool.export_images([3], png_counterpart, fmt="png", dpi=150)
    sz_jpg = os.path.getsize(jpg_path)
    sz_png = os.path.getsize(os.path.join(png_counterpart, "sample_page-4.png"))
    check("JPEG smaller than PNG (gradient)", sz_jpg < sz_png, f"jpg={sz_jpg} png={sz_png}")

    print("== 5. Error cases ==")
    err = None
    try:
        tool.export_images([], outdir)
    except ValueError as e:
        err = e
    check("empty indices -> ValueError", isinstance(err, ValueError), f"got {err}")

    err = None
    try:
        tool.export_images([99], outdir)
    except ValueError as e:
        err = e
    check("out-of-range index -> ValueError", isinstance(err, ValueError), f"got {err}")

    closed_tool = PDFTool()
    err = None
    try:
        closed_tool.export_images([0], outdir)
    except ValueError as e:
        err = e
    check("closed document -> ValueError", isinstance(err, ValueError), f"got {err}")

    tool.close()

    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
