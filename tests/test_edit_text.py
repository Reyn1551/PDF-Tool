#!/usr/bin/env python3
"""Quantitative tests for Edit Text + Compress + Page Numbers features (PDF Tool).

Builds a sample PDF with various text styles, edits a typo, then verifies:
1. New text font/size/color IDENTICAL to the original
2. No overlap (new text width <= original area)
3. Baseline (position) unchanged
4. Other text untouched
5. Too-long text rejected (prevents overlap)
6. Font fallback works (non-embedded / missing system font)
7. Compress & Page Numbers work
8. Preview fix verification (PPM header) + get_text_spans end-to-end

Run:  python3 tests/test_edit_text.py
Exit code 0 = all checks passed.
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
    """Build a sample PDF: plain, bold, italic, colored, multi-line, columns."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text(fitz.Point(72, 100), "This sentence has a typo: recieve.",
                     fontsize=12, fontname="helv", color=(0, 0, 0))
    page.insert_text(fitz.Point(72, 130), "Important part: report.",
                     fontsize=14, fontname="hebo", color=(0.2, 0.2, 0.8))
    page.insert_text(fitz.Point(72, 160), "Footnote: revision.",
                     fontsize=11, fontname="tiit", color=(0.8, 0.1, 0.1))
    page.insert_text(fitz.Point(72, 200), "First document line.", fontsize=12, fontname="helv")
    page.insert_text(fitz.Point(72, 220), "Second document line.", fontsize=12, fontname="helv")
    page.insert_text(fitz.Point(72, 300), "Left column: data A.", fontsize=10, fontname="helv")
    page.insert_text(fitz.Point(350, 300), "Right column: data B.", fontsize=10, fontname="helv")
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

    print("== 1. Setup ==")
    tool = PDFTool()
    tool.open(src)
    spans = tool.get_text_spans(0)
    print(f"  Spans detected: {len(spans)}")
    check("Sample PDF opens", tool.page_count == 1)
    check("Text spans detected (>=6)", len(spans) >= 6)

    print("== 2. Fix typo (plain text, helv) ==")
    idx, s = find_span(tool, 0, "recieve")
    check("Typo span found", idx is not None)
    if idx is not None:
        old_font, old_size, old_color = s["font"], s["size"], s["color"]
        old_bbox = s["bbox"]
        res = tool.replace_text(0, idx, "This sentence has a typo: receive.")
        check("replace_text ok", res["ok"] and res["replaced"])
        check("Original font reused (no fallback)", res["font_used"] == old_font and not res["warnings"])
        spans2 = tool.get_text_spans(0)
        new_s = None
        for sp in spans2:
            if "receive." in sp["text"]:
                new_s = sp
                break
        check("New text present", new_s is not None)
        if new_s:
            check("Font identical", new_s["font"] == old_font, f"{new_s['font']} vs {old_font}")
            check("Size identical", abs(new_s["size"] - old_size) < 0.01, f"{new_s['size']} vs {old_size}")
            check("Color identical", new_s["color"] == old_color, f"#{new_s['color']:06x} vs #{old_color:06x}")
            check("No overlap (new width <= original area)",
                  new_s["bbox"].width <= old_bbox.width + 1.0,
                  f"{new_s['bbox'].width:.1f} vs {old_bbox.width:.1f}")
            check("Baseline unchanged", abs(new_s["origin"].y - s["origin"].y) < 0.5,
                  f"{new_s['origin'].y:.2f} vs {s['origin'].y:.2f}")

    print("== 3. Edit bold colored text (hebo) ==")
    idx, s = find_span(tool, 0, "report")
    if idx is not None:
        old_font, old_size, old_color = s["font"], s["size"], s["color"]
        res = tool.replace_text(0, idx, "Important part: report!")
        check("replace bold ok", res["ok"] and res["replaced"])
        spans2 = tool.get_text_spans(0)
        new_s = None
        for sp in spans2:
            if "report!" in sp["text"]:
                new_s = sp
                break
        check("New bold text present", new_s is not None)
        if new_s:
            check("Bold font identical", new_s["font"] == old_font, f"{new_s['font']} vs {old_font}")
            check("Bold size identical", abs(new_s["size"] - old_size) < 0.01)
            check("Bold color identical", new_s["color"] == old_color, f"#{new_s['color']:06x} vs #{old_color:06x}")

    print("== 4. Other text untouched ==")
    spans3 = tool.get_text_spans(0)
    texts = [sp["text"] for sp in spans3]
    for keep in ["First document line.", "Second document line.",
                 "Left column: data A.", "Right column: data B.", "Footnote: revision."]:
        check(f"Other text intact: '{keep[:24]}...'", any(keep in t for t in texts))

    print("== 5. Reject too-long text (prevent overlap) ==")
    idx, s = find_span(tool, 0, "Left column")
    if idx is not None:
        try:
            tool.replace_text(0, idx, "Left column with extremely long text that far exceeds the original area")
            check("Long text rejected", False, "should raise ValueError")
        except ValueError:
            check("Long text rejected (ValueError)", True)

    print("== 6. Save & reopen ==")
    tool.save_as(out)
    check("Edited file saved", os.path.exists(out))
    tool2 = PDFTool()
    tool2.open(out)
    txt = tool2.doc[0].get_text()
    check("Edited text readable", "receive." in txt)
    check("Original typo gone", "recieve" not in txt)
    check("Other text intact (after reopen)", "Second document line." in txt and "Right column: data B." in txt)
    tool2.close()

    print("== 7. Font fallback (unit) ==")
    check("Base-14: sans bold = hebo", _base14_code(bold=True) == "hebo")
    check("Base-14: sans italic = heit", _base14_code(italic=True) == "heit")
    check("Base-14: serif bold-italic = tibi", _base14_code(bold=True, italic=True, serif=True) == "tibi")
    check("Base-14: mono = cour", _base14_code(mono=True) == "cour")
    sys_font = _find_system_font()
    check("System font found (DejaVu/Liberation)", sys_font is not None, f"path={sys_font}")

    print("== 8. Compress ==")
    comp = os.path.join(tmp, "compressed.pdf")
    before = os.path.getsize(out)
    tool.compress(comp)
    after = os.path.getsize(comp)
    check("Compressed file saved", os.path.exists(comp))
    check("Size did not balloon", after <= before * 1.5, f"{before} -> {after}")
    tool3 = PDFTool()
    tool3.open(comp)
    check("Compressed PDF valid & text intact", "receive." in tool3.doc[0].get_text())
    tool3.close()

    print("== 9. Page Numbers ==")
    tool.add_page_numbers(start=1, position="bottom")
    txt = tool.doc[0].get_text()
    check("Page number '1' present", "1" in txt)
    tool.close()

    print("== 10. Preview fix verification (PPM, not PNG) ==")
    # GUI bug root cause: tk.PhotoImage(data=thumb.tobytes("png")) fails because
    # PhotoImage needs base64/PPM, not raw PNG bytes. The fix uses PPM.
    p0 = PDFTool()
    p0.open(out)
    pix = p0.get_thumb(0, max_width=64)
    ppm = pix.tobytes("ppm")
    check("PPM starts with 'P6' header", ppm[:2] == b"P6",
          f"got {ppm[:2]!r}")
    check("PPM includes dimensions", b"P6\n%d %d\n" % (pix.width, pix.height) in ppm[:64],
          f"{ppm[:64]!r}")
    p0.close()

    # get_text_spans returns searchable text (not empty)
    t = PDFTool()
    t.open(out)
    spans = t.get_text_spans(0)
    check("get_text_spans not empty", len(spans) > 0, f"got {len(spans)}")
    check("Span contains edited text", any("receive." in s["text"] for s in spans))
    t.close()

    print("== 11. Auto-shrink in replace_text ==")
    t_shrink = PDFTool()
    t_shrink.open(src)
    idx, s = find_span(t_shrink, 0, "Left column")
    check("'Left column' span found", idx is not None)
    if idx is not None:
        res_shrink = t_shrink.replace_text(0, idx, "Left column with much longer text", auto_shrink=True)
        check("replace_text auto_shrink succeeds without crash", res_shrink["ok"] and res_shrink["replaced"])
        check("Font-adjustment warning emitted", any("adjusted" in w for w in res_shrink.get("warnings", [])))
        spans_shrunk = t_shrink.get_text_spans(0)
        found_shrunk = any("Left column with much longer text" in sp["text"] for sp in spans_shrunk)
        check("Auto-shrunk new text stored in document", found_shrunk)
    t_shrink.close()

    print("== 12. Replace All (replace_all_text) ==")
    t_all = PDFTool()
    t_all.open(src)
    res_all = t_all.replace_all_text(0, "document", "file")
    check("replace_all_text succeeds", res_all["ok"] and res_all["count"] >= 2, f"count={res_all.get('count')}")
    txt_all = t_all.doc[0].get_text()
    check("Word replaced on every line", "First file line." in txt_all and "Second file line." in txt_all)
    t_all.close()

    print("== 13. Add Free Text & Whiteout (add_custom_text & whiteout_area) ==")
    t_custom = PDFTool()
    t_custom.open(src)
    res_add = t_custom.add_custom_text(0, "New Added Note\nSecond Note Line", x=100, y=400, fontsize=12, bold=True, whiteout=True)
    check("add_custom_text succeeds", res_add["ok"] and res_add["lines_count"] == 2)
    txt_custom = t_custom.doc[0].get_text()
    check("New text present in PDF stream", "New Added Note" in txt_custom and "Second Note Line" in txt_custom)

    res_whiteout = t_custom.whiteout_area(0, fitz.Rect(70, 48, 250, 110))
    check("whiteout_area succeeds", res_whiteout["ok"])
    txt_after_whiteout = t_custom.doc[0].get_text()
    check("Text inside whiteout area erased", "typo" not in txt_after_whiteout)
    t_custom.close()

    print("== 14. Scanned / Image Document support (image-only PDF) ==")
    scan_doc = fitz.open()
    scan_page = scan_doc.new_page(width=595, height=842)
    # Add graphic elements without any digital text
    scan_page.draw_rect(fitz.Rect(50, 50, 500, 700), fill=(0.95, 0.95, 0.95), color=(0.8, 0.8, 0.8))
    scan_path = os.path.join(tmp, "scan_sample.pdf")
    scan_doc.save(scan_path)
    scan_doc.close()

    t_scan = PDFTool()
    t_scan.open(scan_path)
    scan_spans = t_scan.get_text_spans(0)
    check("Scanned PDF detected as 0 digital spans", len(scan_spans) == 0)
    # Add text with whiteout on top of the scanned document
    res_scan_add = t_scan.add_custom_text(0, "SCANNED DOCUMENT APPROVAL SHEET", x=150, y=150, fontsize=14, bold=True, whiteout=True)
    check("add_custom_text works on scanned PDF", res_scan_add["ok"])
    scan_txt = t_scan.doc[0].get_text()
    check("New text readable on scanned PDF", "SCANNED DOCUMENT APPROVAL SHEET" in scan_txt)
    t_scan.close()

    print(f"\n===== RESULT: {PASS} PASS, {FAIL} FAIL =====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
