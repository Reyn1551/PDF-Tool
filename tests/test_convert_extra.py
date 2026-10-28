#!/usr/bin/env python3
"""Regression tests for the conversion slice (``pdf_convert_extra.py``)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from pdf_convert_extra import (
    batch_convert, convert_one, page_to_svg, scan_folder, stats, to_markdown,
    web_optimize,
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


def build_pdf(pages=3, title=True):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        if title and i == 0:
            page.insert_text((60, 90), "Document Title", fontsize=24)
        page.insert_text((60, 140), f"Body paragraph on page {i + 1}.",
                         fontsize=11)
        page.insert_text((60, 160), "Second line of plain body text.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def main():
    tmp = tempfile.mkdtemp(prefix="convert_slice_")
    blob = build_pdf()
    src_pdf = os.path.join(tmp, "sample.pdf")
    with open(src_pdf, "wb") as fh:
        fh.write(blob)

    print("== 1. to_markdown ==")
    md = to_markdown(blob)
    check("markdown output not empty", isinstance(md, str) and md.strip())
    check("markdown ends with newline", md.endswith("\n"), repr(md[-12:]))
    check("body text kept", "Body paragraph on page 1." in md)
    check("large title becomes a heading",
          any(line.lstrip().startswith("#") and "Document Title" in line
              for line in md.splitlines()),
          [l for l in md.splitlines() if "Document Title" in l])
    check("body lines are not headings",
          not any(line.lstrip().startswith("#") and "Body paragraph" in line
                  for line in md.splitlines()))
    check("page separator present", md.count("Body paragraph") == 3,
          md.count("Body paragraph"))

    print("== 2. page_to_svg ==")
    svg = page_to_svg(blob, 0)
    check("svg root element", "<svg" in svg, svg[:60])
    check("svg mentions width", "width=" in svg[:400])
    check("svg non-trivial size", len(svg) > 500, len(svg))
    for bad in (-1, 3):
        try:
            page_to_svg(blob, bad)
        except ValueError:
            check(f"svg rejects page {bad}", True)
        else:
            check(f"svg rejects page {bad}", False)

    print("== 3. web_optimize ==")
    optimised = web_optimize(blob)
    check("optimised output is a PDF", optimised[:4] == b"%PDF", optimised[:8])
    doc = fitz.open(stream=optimised, filetype="pdf")
    check("optimised keeps all pages", doc.page_count == 3, doc.page_count)
    check("optimised keeps text", "Body paragraph" in doc.load_page(0).get_text())
    doc.close()
    five = build_pdf(5)
    chunks = web_optimize(five, subdivide_pages=2)
    check("subdivision returns chunks", isinstance(chunks, list) and len(chunks) == 3,
          chunks if not isinstance(chunks, list) else len(chunks))
    if isinstance(chunks, list):
        check("chunk labels describe ranges",
              [c[0] for c in chunks] == ["pages_1-2", "pages_3-4", "pages_5-5"],
              [c[0] for c in chunks])
        counts = []
        for _label, data in chunks:
            d = fitz.open(stream=data, filetype="pdf")
            counts.append(d.page_count)
            d.close()
        check("chunk page counts sum to total", sum(counts) == 5, counts)
        check("chunks are valid PDFs", all(d[:4] == b"%PDF" for _l, d in chunks))

    print("== 4. stats ==")
    info = stats(blob)
    check("page count", info["pages"] == 3, info)
    check("page size reported", info["page_size"] == (595, 842), info["page_size"])
    check("byte size matches payload", info["bytes"] == len(blob), info["bytes"])
    check("word count plausible", info["words"] >= 18, info["words"])
    check("character count >= word count", info["characters"] >= info["words"])
    check("images counted", info["images"] == 0, info["images"])
    check("not encrypted", info["encrypted"] is False)

    print("== 5. scan_folder ==")
    tree = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(tree, "nested", "deep"), exist_ok=True)
    for rel in ("a.pdf", "b.PDF", "nested/c.pdf", "nested/deep/d.PDF",
                "nested/notes.txt"):
        path = os.path.join(tree, rel)
        with open(path, "wb") as fh:
            fh.write(blob if rel.lower().endswith(".pdf") else b"text")
    found = scan_folder(tree)
    check("recursive scan finds 4 PDFs", len(found) == 4, found)
    check("scan is deterministic (sorted)", found == sorted(found), found)
    check("scan is case-insensitive", any(p.endswith("b.PDF") for p in found))
    check("scan skips other extensions",
          not any(p.endswith(".txt") for p in found))
    check("custom extension list",
          len(scan_folder(tree, extensions=(".txt",))) == 1)

    print("== 6. convert_one ==")
    out = os.path.join(tmp, "out")
    md_path = convert_one(src_pdf, out, "pdf_to_markdown")
    check("markdown file written", os.path.isfile(md_path), md_path)
    with open(md_path, encoding="utf-8") as fh:
        check("markdown file has content", "Body paragraph" in fh.read())
    txt_path = convert_one(src_pdf, out, "pdf_to_text")
    with open(txt_path, encoding="utf-8") as fh:
        check("text export has content", "Body paragraph" in fh.read())
    again = convert_one(src_pdf, out, "pdf_to_markdown")
    check("repeat conversion avoids overwrite", again != md_path, again)
    check("collision suffix applied", "(2)" in os.path.basename(again), again)
    for bad, kind in ((os.path.join(tmp, "ghost.pdf"), "pdf_to_text"), (src_pdf, "nope")):
        try:
            convert_one(bad, out, kind)
        except ValueError:
            check(f"convert_one rejects {kind}", True)
        else:
            check(f"convert_one rejects {kind}", False)

    print("== 7. batch_convert isolation ==")
    from PIL import Image
    batch_dir = os.path.join(tmp, "batch_in")
    os.makedirs(batch_dir, exist_ok=True)
    for name in ("one.png", "two.png"):
        Image.new("RGB", (60, 40), (200, 120, 60)).save(os.path.join(batch_dir, name))
    corrupt = os.path.join(batch_dir, "broken.png")
    with open(corrupt, "wb") as fh:
        fh.write(b"not an image at all")
    sources = [os.path.join(batch_dir, n) for n in ("one.png", "two.png", "broken.png")]
    seen = []
    result = batch_convert(sources, os.path.join(tmp, "batch_out"), "image_to_pdf",
                           progress=lambda d, t, n: seen.append((d, t, n)))
    check("two files converted", len(result["ok"]) == 2, result)
    check("broken file isolated as failure", len(result["failed"]) == 1, result)
    check("failure names the file", result["failed"][0][0] == corrupt, result["failed"])
    check("nothing skipped", result["skipped"] == [], result["skipped"])
    check("progress called per file", len(seen) == 3 and seen[-1][:2] == (3, 3), seen)
    check("outputs exist", all(os.path.isfile(dst) for _src, dst in result["ok"]))

    stop_after = []
    stopped = batch_convert(sources, os.path.join(tmp, "batch_out2"), "image_to_pdf",
                            should_stop=lambda: len(stop_after) >= 0)
    check("should_stop=True skips everything", stopped["ok"] == [], stopped)
    check("skipped list holds all sources", len(stopped["skipped"]) == 3, stopped)

    print(f"\nRESULT: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()