"""Native file picker dialog (zenity) with tkinter fallback.

All functions return a path (str) or list of paths; empty string = user cancelled.
None from _zenity means zenity is not installed -> falls back to tkinter.
"""
import subprocess

import tkinter as tk


def _zenity(args, title):
    # Release active Tkinter grab temporarily to prevent X11 grab deadlock / OS freeze
    grabbed = None
    try:
        if tk._default_root:
            grabbed = tk._default_root.grab_current()
            if grabbed:
                grabbed.grab_release()
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["zenity", "--file-selection", "--title", title] + args,
            capture_output=True, text=True, timeout=600,
        )
        if out.returncode != 0:
            return ""  # Cancelled by user
        return out.stdout.strip()
    except FileNotFoundError:
        return None  # Zenity not installed on system, fall back to filedialog
    except subprocess.TimeoutExpired:
        return ""
    finally:
        if grabbed:
            try:
                if grabbed.winfo_exists():
                    _safe_grab(grabbed)
            except Exception:
                pass


def native_open_file(title, pdf_only=True):
    filt = ["--file-filter", "PDF files (*.pdf) | *.pdf *.PDF"] if pdf_only else ["--file-filter", "All files | *"]
    r = _zenity(filt, title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        ft = [("PDF files", "*.pdf;*.PDF")] if pdf_only else [("All files", "*.*")]
        return filedialog.askopenfilename(title=title, filetypes=ft) or ""
    except Exception:
        return ""


def native_open_files(title):
    r = _zenity(["--multiple", "--separator", "\n"], title)
    if r is not None:
        return [p for p in r.split("\n") if p]
    try:
        from tkinter import filedialog
        files = filedialog.askopenfilenames(title=title, filetypes=[("PDF files", "*.pdf;*.PDF")])
        return list(files) if files else []
    except Exception:
        return []


def native_save_file(title, initial="file.pdf"):
    r = _zenity(["--save", "--filename", initial], title)
    if r is not None:
        if r and not r.lower().endswith(".pdf"):
            r += ".pdf"
        return r
    try:
        from tkinter import filedialog
        res = filedialog.asksaveasfilename(title=title, initialfile=initial, filetypes=[("PDF files", "*.pdf")])
        if res and not res.lower().endswith(".pdf"):
            res += ".pdf"
        return res or ""
    except Exception:
        return ""


def native_open_dir(title):
    r = _zenity(["--directory"], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        return filedialog.askdirectory(title=title) or ""
    except Exception:
        return ""


def native_open_files_images(title):
    r = _zenity(["--multiple", "--separator", "\n",
                 "--file-filter", "Images | *.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp",
                 "--file-filter", "All files | *"], title)
    if r is not None:
        return [p for p in r.split("\n") if p]
    try:
        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title=title,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"), ("All files", "*.*")],
        )
        return list(files) if files else []
    except Exception:
        return []


def native_open_file_office(title):
    r = _zenity(["--file-filter",
                 "Office documents | *.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt *.ods *.odp *.rtf *.csv",
                 "--file-filter", "All files | *"], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        ft = [("Office documents", "*.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt *.ods *.odp *.rtf *.csv"),
              ("All files", "*.*")]
        return filedialog.askopenfilename(title=title, filetypes=ft) or ""
    except Exception:
        return ""


def native_save_file_any(title, initial="output"):
    r = _zenity(["--save", "--filename", initial], title)
    if r is not None:
        return r
    try:
        from tkinter import filedialog
        return filedialog.asksaveasfilename(title=title, initialfile=initial) or ""
    except Exception:
        return ""
