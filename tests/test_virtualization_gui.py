#!/usr/bin/env python3
"""GUI regression tests for the virtualised page grid.

The grid must stay responsive on very long documents: only the visible rows may
own widgets, scrolling must recycle them instead of leaking, selection must
survive a rebuild and search jumps must land on the right page.

Run with ``python3 tests/test_virtualization_gui.py`` (exit 0 = pass, skipped
when no display is available).
"""
import os
import sys
import tempfile
import time

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def make_long_pdf(path, pages=300):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=420)
        page.insert_text((40, 70), f"Page {i + 1}", fontsize=14)
        page.insert_text((40, 100), f"needle-{i + 1}", fontsize=10)
    doc.save(path)
    doc.close()


def pump(root, seconds=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


def main():
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        print("  [SKIP] no display")
        print("VIRTUALIZATION RESULT: 0 PASS, 0 FAIL")
        return 0

    from pdftool.app import PDFToolApp
    from pdftool.theme import ACCENT

    tmp = tempfile.mkdtemp(prefix="virt_test_")
    src = os.path.join(tmp, "long.pdf")
    make_long_pdf(src)

    app = PDFToolApp(root)
    app.tool.open(src)
    app._after_document_change()
    pump(root, 0.5)

    cols = max(1, app._grid_cols)
    budget = cols * 8

    print("== 1. Long document opens with a bounded card set ==")
    check("document opened", app.tool.page_count == 300, app.tool.page_count)
    check("only visible rows are built",
          0 < len(app._cards) <= budget, f"{len(app._cards)} cards, {cols} cols")
    check("scrollregion reserves every row",
          int(float(app.canvas.cget("scrollregion").split()[3])) ==
          app._total_rows() * app._row_h,
          app.canvas.cget("scrollregion"))

    print("== 2. Scrolling far does not leak widgets ==")
    for step in range(30):
        app.canvas.yview_moveto(step / 30)
        pump(root, 0.02)
    pump(root, 0.2)
    check("card set still bounded after a full scroll",
          len(app._cards) <= budget, len(app._cards))
    check("frame child widgets bounded",
          len(app.frame.winfo_children()) <= budget,
          len(app.frame.winfo_children()))

    print("== 3. Scrolled region materialises the pages under the viewport ==")
    app.canvas.yview_moveto(0.5)
    pump(root, 0.2)
    first_row, last_row = app._visible_rows()
    expected = {r * cols + c for r in range(first_row, last_row + 1)
                for c in range(cols)
                if r * cols + c < app.tool.page_count}
    check("visible pages are built", expected.issubset(set(app._cards)),
          f"missing {sorted(expected - set(app._cards))[:5]}")

    print("== 4. Selection survives a rebuild ==")
    page = sorted(app._cards)[0]
    app.tool.toggle_select(page)
    app._refresh_selection_styles()
    app.canvas.yview_moveto(0.95)
    pump(root, 0.2)
    check("page scrolled out of the build window",
          page not in app._cards, sorted(app._cards)[:3])
    app.canvas.yview_moveto(0.0)
    pump(root, 0.2)
    first = min(app._cards)
    app.tool.toggle_select(first)
    app._refresh_selection_styles()
    card = app._cards[first]
    check("rebuilt card is selected", first in app.tool.selection)
    check("selected card shows the accent border",
          card["frame"].cget("highlightbackground") == ACCENT,
          card["frame"].cget("highlightbackground"))
    check("card label matches its page", card["num"].cget("text") == str(first + 1),
          card["num"].cget("text"))

    print("== 5. Search jump lands on the hit page ==")
    hit = app.tool.search_text("needle-250")
    check("search found the deep page", len(hit) == 1, hit)
    if hit:
        app._show_hit(hit[0])
        pump(root, 0.3)
        check("hit page card built", 249 in app._cards,
              f"{sorted(app._cards)[:3]}..{sorted(app._cards)[-3:]}")
        check("status reports the hit", "Page 250" in app.status_label.cget("text"),
              app.status_label.cget("text"))

    print("== 6. Shortcuts do not steal keystrokes from text fields ==")
    root.deiconify()
    root.update()
    entry = tk.Entry(root)
    entry.pack()
    entry.insert(0, "typed text")
    entry.focus_force()
    pump(root, 0.2)
    if root.focus_get() is entry:
        before = set(app.tool.selection)
        entry.event_generate("<Control-a>")
        pump(root, 0.1)
        selected = entry.selection_present() and entry.index(tk.SEL_LAST) == len("typed text")
        check("Ctrl+A selects the entry text", selected)
        check("Ctrl+A leaves the page selection alone",
              set(app.tool.selection) == before, sorted(app.tool.selection))
        entry.event_generate("<Control-z>")
        pump(root, 0.1)
        check("Ctrl+Z in an entry does not undo the document",
              entry.get() == "typed text", entry.get())
    else:
        print("  [SKIP] window manager did not grant focus")
    entry.destroy()
    root.focus_force()
    pump(root, 0.1)

    print("== 7. Zoom relayout keeps the card set bounded ==")
    app.zoom(1)
    pump(root, 0.3)
    check("zoom-in rebuild stays bounded",
          len(app._cards) <= max(1, app._grid_cols) * 8, len(app._cards))
    app.zoom_reset()
    pump(root, 0.3)
    check("zoom reset rebuild stays bounded",
          len(app._cards) <= max(1, app._grid_cols) * 8, len(app._cards))

    app.jobs.shutdown()
    app.thumbs.stop()
    root.destroy()

    print(f"\nVIRTUALIZATION RESULT: {PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())