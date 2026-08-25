#!/usr/bin/env python3
"""Integration and GUI verification test for 'Ke Image' feature in pdf_tool.py."""
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
        p.insert_text((50, 80), f"Halaman {i + 1}", fontsize=16)
    doc.save(path)
    doc.close()


def main():
    root = tk.Tk()
    root.withdraw()

    tmp_dir = tempfile.mkdtemp(prefix="gui_pdf2img_test_")
    pdf_path = os.path.join(tmp_dir, "test_doc.pdf")
    make_sample_pdf(pdf_path)

    app = PDFToolApp(root)

    print("== 1. Verifikasi Sidebar Item Ke Image ==")
    ke_image_item = None
    for item in app.sidebar_items:
        if item.text == "PDF \u2192 Image (JPG/PNG)":
            ke_image_item = item
            break
    check("Sidebar item 'PDF → Image' terdaftar", ke_image_item is not None)
    check("'PDF → Image' disabled saat belum buka PDF", ke_image_item._enabled is False)

    print("== 2. Buka PDF & status enable ==")
    app.tool.open(pdf_path)
    app._sync_enabled()
    check("'PDF → Image' enabled setelah buka PDF", ke_image_item._enabled is True)

    print("== 3. Buka Dialog Ekspor ke Image ==")
    with patch("pdftool.actions_export._safe_grab", side_effect=lambda w: w):
        app.export_to_image()

    top_dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    check("Dialog Toplevel muncul", len(top_dialogs) == 1)
    dlg = top_dialogs[0]
    check("Title dialog sesuai", dlg.title() == "Ekspor ke Image")

    quality_entry = None
    for child in dlg.winfo_children():
        if isinstance(child, tk.Frame):
            for sub in child.winfo_children():
                if isinstance(sub, tk.Entry):
                    quality_entry = sub

    check("Quality entry ditemukan", quality_entry is not None)
    check("Quality entry disabled saat default PNG", str(quality_entry.cget("state")) in (str(tk.DISABLED), "disabled"))

    print("== 4. Ekspor Semua Halaman PNG via handler ==")
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
        check("Messagebox info muncul", mock_info.called)

    files_png = sorted(os.listdir(out_all_png))
    check("4 file PNG tersimpan", len(files_png) == 4, f"got {files_png}")
    check("Format nama file sesuai", files_png[0] == "test_doc_halaman-1.png")

    print("== 5. Ekspor Subset Halaman JPEG (Halaman 2 & 4) ==")
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
        check("Messagebox sukses dipanggil untuk JPEG", mock_info.called)

    files_jpg = sorted(os.listdir(out_sub_jpg))
    check("Subset 2 file JPEG", len(files_jpg) == 2, f"got {files_jpg}")
    check("Halaman 2 dan 4 terekspor", files_jpg == ["test_doc_halaman-2.jpg", "test_doc_halaman-4.jpg"])

    pix = fitz.Pixmap(os.path.join(out_sub_jpg, files_jpg[0]))
    exp_w = round(400 * 300 / 72)
    check("Dimensi JPEG 300 DPI akurat (~1667px)", abs(pix.width - exp_w) <= 1, f"got {pix.width}")

    print("== 6. Error handling pada input kualitas tidak valid ==")
    with patch("pdftool.actions_export._safe_grab", side_effect=lambda w: w):
        app.export_to_image()
    dlg3 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
    with patch("pdftool.actions_export.native_open_dir", return_value=tmp_dir), \
         patch("pdftool.actions_export.messagebox.showerror") as mock_err:
        q_entry_bad = tk.Entry(dlg3)
        q_entry_bad.insert(0, "abc")
        app._do_export_images(dlg3, tk.StringVar(value="jpeg"), q_entry_bad, tk.StringVar(value="150"))
        check("Errorbox jika kualitas bukan angka", mock_err.called)
    dlg3.destroy()

    app.tool.close()
    root.destroy()

    print(f"\nHASIL INTEGRASI: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
