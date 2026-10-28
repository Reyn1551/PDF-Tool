#!/usr/bin/env python3
"""Regression tests for the page-operation slice (``pdf_ops_pages.py``).

Run with ``python3 tests/test_slices_pages.py`` (exit 0 = pass).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from pdf_ops_pages import (
    apply_crop_boxes, auto_crop, blank_pages, booklet, booklet_order,
    content_boxes, delete_pages, extract_pages, nup, parse_ranges,
    split_every, split_ranges, validate_range_spec,
)

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


def build_pdf(pages, blank=()):
    """PDF whose page N carries the text ``Page N`` (0-based indexes)."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        if i not in blank:
            page.insert_text((40, 60), f"Page {i}", fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def reopen(data):
    return fitz.open(stream=data, filetype="pdf")


def main():
    tmp = tempfile.mkdtemp(prefix="pages_slice_")
    blob = build_pdf(7)

    print("== 1. parse_ranges / validate_range_spec ==")
    check("single page", parse_ranges("3", 7) == [(2, 2)], parse_ranges("3", 7))
    check("open-ended range", parse_ranges("5-", 7) == [(4, 6)], parse_ranges("5-", 7))
    check("grouped list", parse_ranges("1,3,5-7", 7) == [(0, 0), (2, 2), (4, 6)],
          parse_ranges("1,3,5-7", 7))
    check("spaces ignored", parse_ranges(" 1 - 2 , 4 ", 7) == [(0, 1), (3, 3)])
    for bad in ("0", "8", "5-3", "abc", ""):
        try:
            parse_ranges(bad, 7)
        except ValueError:
            check(f"rejects {bad!r}", True)
        else:
            check(f"rejects {bad!r}", False, "no ValueError")
    check("validate accepts good spec", validate_range_spec("1-3,5", 7))
    check("validate rejects reversed range", not validate_range_spec("5-3", 7))
    check("validate rejects garbage", not validate_range_spec("1,,2", 7))

    print("== 2. extract_pages ==")
    part = reopen(extract_pages(blob, [0, 3, 6]))
    check("extract keeps requested count", part.page_count == 3, part.page_count)
    check("extract keeps page order", "Page 3" in part.load_page(1).get_text())
    part.close()
    dup = reopen(extract_pages(blob, [2, 2]))
    check("extract allows duplicates", dup.page_count == 2, dup.page_count)
    dup.close()
    for bad in ([], [7], [-1]):
        try:
            extract_pages(blob, bad)
        except ValueError:
            check(f"extract rejects {bad}", True)
        else:
            check(f"extract rejects {bad}", False, "no ValueError")

    print("== 3. split_every ==")
    chunks = split_every(blob, 3)
    check("7 pages by 3 -> 3 files", len(chunks) == 3, len(chunks))
    check("labels describe ranges",
          [c[0] for c in chunks] == ["pages_1-3", "pages_4-6", "page_7"],
          [c[0] for c in chunks])
    counts = [reopen(c[1]).page_count for c in chunks]
    check("chunk page counts sum to total", sum(counts) == 7, counts)
    check("chunk counts exact", counts == [3, 3, 1], counts)
    try:
        split_every(blob, 0)
    except ValueError:
        check("split_every rejects 0 pages", True)
    else:
        check("split_every rejects 0 pages", False)

    print("== 4. split_ranges ==")
    parts = split_ranges(blob, [(0, 1), (5, 6)])
    check("split_ranges makes one file per range", len(parts) == 2, len(parts))
    check("split_ranges labels", parts[0][0] == "pages_1-2", parts[0][0])
    check("split_ranges sizes", [reopen(p[1]).page_count for p in parts] == [2, 2])
    try:
        split_ranges(blob, [(3, 1)])
    except ValueError:
        check("split_ranges rejects inverted range", True)
    else:
        check("split_ranges rejects inverted range", False)

    print("== 5. nup imposition ==")
    for per_sheet, expected in ((2, 4), (4, 2)):
        sheet_doc = reopen(nup(blob, per_sheet=per_sheet))
        check(f"{per_sheet}-up page count", sheet_doc.page_count == expected,
              sheet_doc.page_count)
        first = sheet_doc.load_page(0).rect
        check(f"{per_sheet}-up keeps source sheet size",
              abs(first.width - 300) < 1 and abs(first.height - 400) < 1,
              (first.width, first.height))
        sheet_doc.close()
    try:
        nup(blob, per_sheet=3)
    except ValueError:
        check("nup rejects unsupported grid", True)
    else:
        check("nup rejects unsupported grid", False)
    try:
        nup(blob, per_sheet=2, margin=400)
    except ValueError:
        check("nup rejects oversized margins", True)
    else:
        check("nup rejects oversized margins", False)

    print("== 6. booklet ordering ==")
    check("empty document -> no sheets", booklet_order(0) == [])
    order = booklet_order(5)
    check("5 pages pad to 8 -> 2 sheets", len(order) == 2, len(order))
    check("padding slots are blank for 5 pages",
          any(None in side for sheet in order for side in sheet), order)
    check("8-page first side pairs last and first",
          booklet_order(8)[0][0] == (7, 0), booklet_order(8)[0][0])
    padded = booklet_order(8)
    check("8 pages need no padding",
          all(None not in side for sheet in padded for side in sheet), padded)
    flat = [idx for sheet in padded for side in sheet for idx in side if idx is not None]
    check("every source page appears once", sorted(flat) == list(range(8)), sorted(flat))
    book = reopen(booklet(blob))
    check("7 pages -> 2 sheets -> 4 printed sides", book.page_count == 4,
          book.page_count)
    side = book.load_page(0).rect
    check("booklet sheet is two pages wide", abs(side.width - 600) < 1, side.width)
    book.close()

    print("== 7. blank page detection ==")
    mixed = build_pdf(4, blank=(1,))
    blanks = blank_pages(mixed)
    check("detects the single blank page", blanks == [1], blanks)
    check("page subset honoured", blank_pages(mixed, pages=[2, 3]) == [])
    check("no false positives on text pages", blank_pages(blob) == [], blank_pages(blob))

    print("== 8. cropping ==")
    padded = fitz.open()
    page = padded.new_page(width=400, height=500)
    page.insert_text((150, 250), "centred content", fontsize=48)
    padded_blob = padded.tobytes()
    padded.close()
    boxes = content_boxes(padded_blob)
    rect = boxes.get(0)
    check("content box found", rect is not None and rect.width < 400, rect)
    if rect is not None:
        check("content box surrounds ink", rect.x0 < 150 < rect.x1,
              (rect.x0, rect.x1))
    cropped, changed = apply_crop_boxes(padded_blob, boxes)
    check("apply_crop_boxes reports changed pages", changed == 1, changed)
    doc = reopen(cropped)
    check("crop box shrinks page", doc.load_page(0).rect.width < 400,
          doc.load_page(0).rect.width)
    doc.close()
    auto_blob, auto_changed = auto_crop(padded_blob)
    check("auto_crop changes the padded page", auto_changed == 1, auto_changed)
    check("auto_crop leaves a mostly-inked page alone",
          auto_crop(build_pdf(1), threshold=0.0001)[1] == 0)

    print("== 9. delete_pages ==")
    fewer = reopen(delete_pages(blob, [1, 5]))
    check("delete removes both pages", fewer.page_count == 5, fewer.page_count)
    check("delete keeps surviving text", "Page 2" in fewer.load_page(1).get_text())
    fewer.close()
    try:
        delete_pages(blob, [])
    except ValueError:
        check("delete rejects empty selection", True)
    else:
        check("delete rejects empty selection", False)

    print(f"\nRESULT: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()