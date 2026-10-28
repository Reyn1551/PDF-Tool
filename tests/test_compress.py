#!/usr/bin/env python3
"""Tests for the compression engine (preset + target-size search).

Covers:
A. Preset single-pass compression  : smaller output, text preserved.
B. Target-size search              : hits achievable targets, lossless-first,
                                     honest miss on impossible targets.
C. Safety                          : live document untouched, legacy API intact.

Run:  python3 tests/test_compress.py
Exit code 0 = all checks passed.
"""
import os
import shutil
import sys
import tempfile

import fitz
from PIL import Image, ImageFilter

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


def make_photo_pdf(path, n_pages=3):
    """PDF with photo-like images + real text (worst case for naive compress)."""
    d = tempfile.mkdtemp(prefix="pdftool_c_")
    img_path = os.path.join(d, "photo.jpg")
    img = Image.new("RGB", (2000, 1500))
    px = img.load()
    for y in range(0, 1500, 2):
        for x in range(0, 2000, 2):
            c = ((x * 255) // 2000, (y * 255) // 1500, ((x + y) * 255) // 3500)
            px[x, y] = c
            if x + 1 < 2000:
                px[x + 1, y] = c
            if y + 1 < 1500:
                px[x, y + 1] = c
                px[x + 1, y + 1] = c
    img = img.filter(ImageFilter.GaussianBlur(1.5))
    img.save(img_path, quality=95)
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=img_path)
        page.insert_text((72, 72), f"Searchable text {i + 1}", fontsize=14)
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    shutil.rmtree(d, ignore_errors=True)
    return path


def main():
    d = tempfile.mkdtemp(prefix="pdftool_test_compress_")
    try:
        _run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n===== RESULT: {PASS} PASS, {FAIL} FAIL =====")
    return 0 if FAIL == 0 else 1


def _run(d):
    src = make_photo_pdf(os.path.join(d, "src.pdf"))
    size_before = os.path.getsize(src)
    t = PDFTool()
    t.open(src)

    # --- A. Preset single-pass ---
    out_balanced = os.path.join(d, "balanced.pdf")
    t.compress(out_balanced, preset="balanced")
    check("preset balanced produces smaller file",
          os.path.getsize(out_balanced) < size_before)
    doc_b = fitz.open(out_balanced)
    check("preset keeps text searchable",
          "Searchable text 1" in doc_b[0].get_text())
    check("preset keeps page count", doc_b.page_count == 3)
    doc_b.close()

    out_light = os.path.join(d, "light.pdf")
    t.compress(out_light, preset="light")
    out_extreme = os.path.join(d, "extreme.pdf")
    t.compress(out_extreme, preset="extreme")
    check("light preset larger than extreme preset",
          os.path.getsize(out_light) > os.path.getsize(out_extreme))

    # --- B. Target-size search ---
    # Lossless baseline already fits -> zero quality loss, no image rewrite.
    big_target = os.path.join(d, "big.pdf")
    res = t.compress_to_size(big_target, target_mb=size_before / (1024 * 1024) * 10)
    check("generous target met losslessly", res["target_reached"] and res["params_used"] is None,
          str(res))
    check("lossless result still smaller or equal",
          res["size_after"] <= size_before)

    # Achievable but tight target -> search engages and hits it.
    tight_mb = max(size_before * 0.25 / (1024 * 1024), 0.05)
    tight_out = os.path.join(d, "tight.pdf")
    steps = []
    res2 = t.compress_to_size(tight_out, target_mb=tight_mb, preset="balanced",
                              progress=steps.append)
    check("tight target reached", res2["target_reached"],
          f"{res2['size_after']} vs {int(tight_mb*1024*1024)}")
    check("search reported progress", len(steps) > 0)
    doc_t = fitz.open(tight_out)
    check("tight result keeps text searchable",
          "Searchable text 1" in doc_t[0].get_text())
    check("tight result keeps pages", doc_t.page_count == 3)
    doc_t.close()

    # Impossible target -> honest miss, still returns smallest safe output.
    res3 = t.compress_to_size(os.path.join(d, "tiny.pdf"), target_mb=0.0005, preset="extreme")
    check("impossible target reported honestly", res3["target_reached"] is False)
    check("impossible target still shrinks file", res3["size_after"] < size_before)

    # Invalid input rejected.
    try:
        t.compress_to_size(os.path.join(d, "neg.pdf"), target_mb=-1)
        check("negative target rejected", False)
    except ValueError:
        check("negative target rejected", True)

    # --- C. Safety / legacy ---
    check("live document untouched by compress_to_size", t.page_count == 3)
    legacy = os.path.join(d, "legacy.pdf")
    fitz.open(src).save(legacy)
    t2 = PDFTool()
    t2.open(legacy)
    t2.compress()  # old signature: no args, overwrite original
    check("legacy compress() with no args still works", os.path.getsize(legacy) > 0)

    # Floors respected: never below quality floor even at t=1.0.
    params = PDFTool._interpolate_params("extreme", 1.0)
    check("quality floor respected",
          params["quality"] >= PDFTool._MIN_QUALITY and params["dpi_target"] >= PDFTool._MIN_DPI)


if __name__ == "__main__":
    sys.exit(main())
