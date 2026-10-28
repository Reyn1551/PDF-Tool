#!/usr/bin/env python3
"""Regression tests for the stability layer: undo/redo, snapshots, search,
thumbnail cache, job runner and the converted-file helpers.

Run with ``python3 tests/test_core_stability.py`` (exit 0 = pass).
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from pdf_ops import PDFTool
from pdf_ops_pages import extract_pages, split_every, parse_ranges, blank_pages
from pdf_ops_security import find_matches, redact, sanitize
from pdf_convert_extra import to_markdown, page_to_svg, web_optimize, stats
from pdftool.thumbs import ThumbCache
from pdftool.jobs import JobRunner, JobCancelled
from pdftool.dnd import _split_drop
from pdftool.recent import RecentStore

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


def make_pdf(path, pages=6, marker="hello"):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=400, height=600)
        page.insert_text((50, 80), f"{marker} page {i + 1}", fontsize=14)
        page.insert_text((50, 120), "second line body text", fontsize=10)
        if i == 0:
            page.insert_text((50, 170), "SECRET-TOKEN", fontsize=12)
    doc.save(path)
    doc.close()


def main():
    tmp = tempfile.mkdtemp(prefix="core_test_")
    src = os.path.join(tmp, "src.pdf")
    make_pdf(src)

    print("== 1. Undo / redo / dirty tracking ==")
    tool = PDFTool()
    tool.open(src)
    check("opens 6 pages", tool.page_count == 6)
    check("clean after open", tool.dirty is False)
    tool.toggle_select(0)
    tool.delete_pages([0])
    check("delete removes page", tool.page_count == 5, tool.page_count)
    check("dirty after mutation", tool.dirty is True)
    check("undo available", tool.can_undo)
    tool.undo()
    check("undo restores page count", tool.page_count == 6, tool.page_count)
    check("redo available", tool.can_redo)
    tool.redo()
    check("redo deletes again", tool.page_count == 5, tool.page_count)
    tool.undo()

    print("== 2. Snapshot round-trip (worker handoff) ==")
    data = tool.snapshot_bytes()
    clone = PDFTool.from_bytes(data, source_name="src")
    check("detached copy keeps page count", clone.page_count == 6, clone.page_count)
    check("detached copy keeps name hint", clone.source_name == "src")
    check("detached copy is independent", clone is not tool)
    clone.close()

    print("== 3. Transactional batch rolls back on error ==")
    before = tool.page_count
    try:
        with tool.batch("Explode"):
            tool.rotate([0], 90)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check("batch rollback restores page count", tool.page_count == before)

    print("== 4. Search ==")
    hits = tool.search_text("hello page 3")
    check("search finds text", len(hits) >= 1, len(hits))
    check("hit reports 0-based page", hits and hits[0]["page"] == 2,
          hits[:1])
    check("no hits for gibberish", tool.search_text("zzz-not-there") == [])

    print("== 5. Page operations ==")
    check("parse_ranges expands groups",
          parse_ranges("1,3,5-6", 6) == [(0, 0), (2, 2), (4, 5)],
          parse_ranges("1,3,5-6", 6))
    blob = tool.snapshot_bytes()
    extracted = extract_pages(blob, [0, 1])
    part = fitz.open(stream=extracted, filetype="pdf")
    check("extract_pages keeps 2 pages", part.page_count == 2, part.page_count)
    part.close()
    chunks = split_every(blob, 2)
    check("split_every yields 3 chunks", len(chunks) == 3, len(chunks))
    check("split_every labels ranges", chunks[1][0] == "pages_3-4", chunks[1][0])
    blanks = blank_pages(blob)
    check("no blank pages detected in text file", blanks == [], blanks)

    print("== 6. Redaction & sanitising ==")
    matches = find_matches(blob, terms=["SECRET-TOKEN"])
    check("redaction finds token", len(matches) >= 1, len(matches))
    redacted, report = redact(blob, terms=["SECRET-TOKEN"])
    check("redact reports hits", report["hits"] >= 1, report)
    doc = fitz.open(stream=redacted, filetype="pdf")
    text = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    doc.close()
    check("redaction removes the text", "SECRET-TOKEN" not in text)
    cleaned, sreport = sanitize(blob)
    check("sanitize returns valid PDF", cleaned[:4] == b"%PDF")
    check("sanitize reports metadata keys", isinstance(sreport.get("metadata"), dict),
          sreport)

    print("== 7. Conversion helpers ==")
    md = to_markdown(blob)
    check("markdown has body text", "second line body text" in md)
    svg = page_to_svg(blob, 0)
    check("svg output", "<svg" in svg)
    opt = web_optimize(blob)
    check("web_optimize returns PDF", opt[:4] == b"%PDF")
    parts = web_optimize(blob, subdivide_pages=2)
    check("web_optimize subdivides", isinstance(parts, list) and len(parts) == 3, len(parts))
    info = stats(blob)
    check("stats counts pages/words", info["pages"] == 6 and info["words"] > 0, info)

    print("== 8. Thumbnail LRU ==")
    cache = ThumbCache(limit=8)
    limit = cache.limit
    for i in range(limit):
        cache.put(cache.key(0, i, 140), (i, 1, 1))
    cache.get(cache.key(0, 0, 140))          # refresh the oldest entry
    cache.put(cache.key(0, limit, 140), (limit, 1, 1))   # evicts entry 1
    check("LRU keeps recent entry", cache.get(cache.key(0, 0, 140)) is not None)
    check("LRU evicts oldest", cache.get(cache.key(0, 1, 140)) is None)
    check("LRU bounded", len(cache) == limit, len(cache))
    cache.invalidate(keep_generation=1)
    check("invalidate drops old generation", len(cache) == 0, len(cache))

    print("== 9. Job runner (headless) ==")
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        print("  [SKIP] no display")
    else:
        runner = JobRunner(root, max_concurrent=2)
        seen = {}
        job = runner.submit("t", lambda j: (time.sleep(0.05), "done")[1],
                            on_done=lambda r: seen.setdefault("result", r),
                            title=None)
        runner.wait(10)
        check("job result delivered on main thread", seen.get("result") == "done", seen)

        def slow(j):
            for _ in range(50):
                j.check()
                time.sleep(0.01)
            return "finished"
        cancelled = {}
        job2 = runner.submit("c", slow,
                             on_cancel=lambda: cancelled.setdefault("hit", True),
                             title=None)
        job2.cancel()
        runner.wait(10)
        check("cancelled job reports on_cancel", cancelled.get("hit") is True, cancelled)
        runner.shutdown()
        root.destroy()

    print("== 10. Recent store + drop parser ==")
    store = RecentStore(os.path.join(tmp, "state.json"))
    store.add_recent(src)
    check("recent stores path", src in store.recent(), store.recent())
    check("drop parser handles braces",
          _split_drop("{" + src + "} /tmp/missing.pdf") == [src])

    tool.close()

    print(f"\nCORE RESULT: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
