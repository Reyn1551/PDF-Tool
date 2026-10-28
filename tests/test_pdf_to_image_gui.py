#!/usr/bin/env python3
"""Integration & GUI verification test for the 'Export to Image' feature."""
import os
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdf_tool
from pdf_tool import PDFToolApp

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


def make_sample_pdf(path):
    doc = fitz.open()
    for i in range(4):
        p = doc.new_page(width=400, height=600)
        p.insert_text((50, 80), f"Page {i + 1}", fontsize=16)
    doc.save(path)
    doc.close()


def main():
    root = tk.Tk()
    root.withdraw()

    tmp_dir = tempfile.mkdtemp(prefix="gui_pdf2img_test_")
    pdf_path = os.path.join(tmp_dir, "test_doc.pdf")
    make_sample_pdf(pdf_path)

    app = PDFToolApp(root)

    print("== 1. Verify PDF -> Image sidebar item ==")
    to_image_item = None
    for item in app.sidebar_items:
        if item.text == "PDF \u2192 Image (JPG/PNG)":
            to_image_item = item
            break
    check("Sidebar item 'PDF → Image' registered", to_image_item is not None)
    check("'PDF → Image' disabled before opening a PDF", to_image_item._enabled is False)

    print("== 2. Open PDF & enabled state ==")
    app.tool.open(pdf_path)
    app._sync_enabled()
    check("'PDF → Image' enabled after opening a PDF", to_image_item._enabled is True)

    print("== 3. Open the Export to Image dialog ==")
    with patch("pdftool.actions_export._safe_grab", side_effect=lambda w: w):
        app.export_to_image()

    top_dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    check("Toplevel dialog appears", len(top_dialogs) == 1)
    dlg = top_dialogs[0]
    check("Dialog title matches", dlg.title() == "Export to Image")

    quality_entry = None
    for child in dlg.winfo_children():
        if isinstance(child, tk.Frame):
            for sub in child.winfo_children():
                if isinstance(sub, tk.Entry):
                    quality_entry = sub

    check("Quality entry found", quality_entry is not None)
    check("Quality entry disabled for default PNG", str(quality_entry.cget("state")) in (str(tk.DISABLED), "disabled"))

    print("== 4. Export all pages to PNG via handler ==")
    out_all_png = os.path.join(tmp_dir, "out_all_png")
    os.makedirs(out_all_png, exist_ok=True)

    with patch("pdftool.actions_export.native_open_dir", return_value=out_all_png), \
         patch("pdftool.actions_export.messagebox.showinfo") as mock_info:
        var_fmt = tk.StringVar(value="png")
        var_dpi = tk.StringVar(value="150")
        quality_entry.config(state=tk.NORMAL)
        quality_entry.delete(0, tk.END)
        quality_entry.insert(0, "90")

        app._do_export_images(dlg, var_fmt, quality_entry, var_dpi)
        app.jobs.wait()
        check("Info messagebox shown", mock_info.called)

    files_png = sorted(os.listdir(out_all_png))
    check("4 PNG files saved", len(files_png) == 4, f"got {files_png}")
    check("Filename format correct", files_png[0] == "test_doc_page-1.png")

    print("== 5. Export page subset to JPEG (pages 2 & 4) ==")
    with patch("pdftool.actions_export._safe_grab", side_effect=lambda w: w):
        app.tool.clear_selection()
        app.tool.toggle_select(1)
        app.tool.toggle_select(3)
        app.export_to_image()

    dlgs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    dlg2 = dlgs[0]
    out_sub_jpg = os.path.join(tmp_dir, "out_sub_jpg")
    os.makedirs(out_sub_jpg, exist_ok=True)

    with patch("pdftool.actions_export.native_open_dir", return_value=out_sub_jpg), \
         patch("pdftool.actions_export.messagebox.showinfo") as mock_info:
        var_fmt = tk.StringVar(value="jpeg")
        var_dpi = tk.StringVar(value="300")
        q_entry = tk.Entry(dlg2)
        q_entry.insert(0, "85")

        app._do_export_images(dlg2, var_fmt, q_entry, var_dpi)
        app.jobs.wait()
        check("Success messagebox called for JPEG", mock_info.called)

    files_jpg = sorted(os.listdir(out_sub_jpg))
    check("JPEG subset = 2 files", len(files_jpg) == 2, f"got {files_jpg}")
    check("Pages 2 and 4 exported", files_jpg == ["test_doc_page-2.jpg", "test_doc_page-4.jpg"])

    pix = fitz.Pixmap(os.path.join(out_sub_jpg, files_jpg[0]))
    exp_w = round(400 * 300 / 72)
    check("300 DPI JPEG dimensions accurate (~1667px)", abs(pix.width - exp_w) <= 1, f"got {pix.width}")

    print("== 6. Error handling for invalid quality input ==")
    with patch("pdftool.actions_export._safe_grab", side_effect=lambda w: w):
        app.export_to_image()
    dlg3 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
    with patch("pdftool.actions_export.native_open_dir", return_value=tmp_dir), \
         patch("pdftool.actions_export.messagebox.showerror") as mock_err:
        q_entry_bad = tk.Entry(dlg3)
        q_entry_bad.insert(0, "abc")
        app._do_export_images(dlg3, tk.StringVar(value="jpeg"), q_entry_bad, tk.StringVar(value="150"))
        check("Errorbox when quality is not a number", mock_err.called)
    dlg3.destroy()

    app.tool.close()
    root.destroy()

    print(f"\nINTEGRATION RESULT: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
