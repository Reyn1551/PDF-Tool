"""Page organization actions: open, delete, merge, split, rotate, reorder, crop."""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

import fitz

from .theme import BG, TEXT, TEXT_MUTED, ACCENT, BORDER, SURFACE, DANGER, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, SidebarItem, _safe_grab, _set_app_icon
from .dialogs import (
    native_open_file, native_open_files, native_save_file, native_open_dir,
)
from pdf_ops import PDFTool


class OrganizeActions:
    """Mixin — combined into PDFToolApp via multiple inheritance."""

    def open_pdf(self):
        path = native_open_file("Select PDF")
        if not path:
            return
        password = None
        for attempt in range(3):  # maximum 3 password attempts
            try:
                self.tool.open(path, password)
                break
            except ValueError as e:
                if str(e) == "NEEDS_PASSWORD":
                    password = simpledialog.askstring(
                        "Password",
                        "This PDF is password protected.\nEnter password"
                        + (f"  (attempt {attempt + 1}/3)" if attempt else "")
                        + ":",
                        show="*", parent=self.root,
                    )
                    if password is None:
                        return  # user cancelled
                else:
                    messagebox.showerror("PDF Tool", str(e), parent=self.root)
                    return
        else:
            messagebox.showerror("PDF Tool", "Wrong password 3 times.", parent=self.root)
            return
        self._refresh_preview()

    def select_all(self):
        if not self._require_doc():
            return
        self.tool.select_all()
        self._refresh_preview()

    def delete_pages(self):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Select pages to delete first.", parent=self.root)
            return
        if not messagebox.askyesno("Delete Pages", f"Delete {len(self.tool.selection)} selected page(s)?", parent=self.root):
            return
        try:
            self.tool.delete_pages(self.tool.selection)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def merge_pdf(self):
        if not self._require_doc():
            return
        paths = native_open_files("Select PDF to merge")
        if not paths:
            return
        try:
            self.tool.merge(list(paths))
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Failed to merge: {e}", parent=self.root)
            return
        self._refresh_preview()

    def insert_pages(self):
        if not self._require_doc():
            return
        path = native_open_file("Select source PDF")
        if not path:
            return
        try:
            other = PDFTool()
            other.open(path)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        total = other.page_count
        other.close()
        sel_src = simpledialog.askstring(
            "Insert from Another PDF",
            f"Source PDF has {total} page(s).\nPage numbers to insert (comma-separated, e.g.: 1,3,5-7):",
            parent=self.root,
        )
        if not sel_src:
            return
        try:
            idx = self._parse_range(sel_src, total)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        pos = simpledialog.askinteger(
            "Insert from Another PDF",
            f"Insert position in main PDF (1-{self.tool.page_count + 1}):",
            initialvalue=self.tool.page_count + 1, minvalue=1, maxvalue=self.tool.page_count + 1,
            parent=self.root,
        )
        if pos is None:
            return
        try:
            self.tool.insert_from(path, idx, pos - 1)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def split_pdf(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Split PDF")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        var = tk.StringVar(value="range")
        tk.Radiobutton(dlg, text="Page range (e.g.: 3-7 or 1,3,5-8)", variable=var, value="range",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Radiobutton(dlg, text="All pages (each page as separate file)", variable=var, value="all",
                       bg=BG, fg=TEXT, font=FONT, activebackground=BG, selectcolor=SURFACE).pack(anchor="w", padx=16, pady=2)
        entry = tk.Entry(dlg, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry.pack(padx=16, pady=(8, 12), fill=tk.X)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(0, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Split", command=lambda: self._do_split(dlg, var.get(), entry.get()), primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _do_split(self, dlg, mode, rng):
        dlg.destroy()
        try:
            if mode == "all":
                outdir = native_open_dir("Destination folder")
                if not outdir:
                    return
                for i in range(self.tool.page_count):
                    single = fitz.open()
                    single.insert_pdf(self.tool.doc, from_page=i, to_page=i)
                    single.save(os.path.join(outdir, f"page_{i+1}.pdf"))
                    single.close()
            else:
                if not rng:
                    raise ValueError("Enter page range first (e.g.: 3-7 or 1,3,5-8).")
                idx = self._parse_range(rng, self.tool.page_count)
                path = native_save_file("Save split result", initial="split.pdf")
                if not path:
                    return
                out = fitz.open()
                for i in idx:
                    out.insert_pdf(self.tool.doc, from_page=i, to_page=i)
                out.save(path)
                out.close()
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Split failed: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF Tool", "Split complete.", parent=self.root)

    def rotate(self, angle):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Select pages to rotate first.", parent=self.root)
            return
        try:
            self.tool.rotate(self.tool.selection, angle)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def move_pages(self):
        if not self._require_doc():
            return
        if not self.tool.selection:
            messagebox.showinfo("PDF Tool", "Select pages to move first.", parent=self.root)
            return
        n = self.tool.page_count - len(self.tool.selection)
        target = simpledialog.askinteger(
            "Reorder Pages",
            f"Target position (1-{n + 1}). Example: move pages 3,5 to position 1:",
            initialvalue=1, minvalue=1, maxvalue=n + 1, parent=self.root,
        )
        if target is None:
            return
        try:
            self.tool.move_pages(self.tool.selection, target - 1)
        except ValueError as e:
            messagebox.showerror("PDF Tool", str(e), parent=self.root)
            return
        self._refresh_preview()

    def save_as(self):
        if not self._require_doc():
            return
        path = native_save_file("Save PDF")
        if not path:
            return
        try:
            self.tool.save_as(path)
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Failed to save: {e}", parent=self.root)
            return
        messagebox.showinfo("PDF Tool", f"Saved: {path}", parent=self.root)

    def reverse_pages(self):
        if not self._require_doc():
            return
        try:
            self.tool.reverse_pages()
        except Exception as e:
            messagebox.showerror("PDF Tool", f"Failed to reverse order: {e}", parent=self.root)
            return
        self._refresh_preview()

    # ---------- New feature: Crop ----------
    def crop_pages_ui(self):
        if not self._require_doc():
            return
        n_sel = len(self.tool.selection)
        info = f"{n_sel} page(s) selected." if n_sel else "All pages."
        dlg = tk.Toplevel(self.root)
        dlg.title("Crop Pages")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text=f"Crop edges: {info}", font=FONT_BOLD, bg=BG, fg=TEXT).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(dlg, text="Crop ratio per side (0 - 0.45 of page size):",
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16)
        row = tk.Frame(dlg, bg=BG)
        row.pack(padx=16, pady=8)
        vars_ = {}
        for i, (label, default) in enumerate((("Left", "0"), ("Top", "0"), ("Right", "0"), ("Bottom", "0"))):
            tk.Label(row, text=label, font=FONT_SMALL, bg=BG, fg=TEXT).grid(row=0, column=i, padx=(0, 4))
            e = tk.Entry(row, width=6, font=FONT_SMALL, relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=BORDER, justify=tk.CENTER)
            e.insert(0, default)
            e.grid(row=1, column=i, padx=(0, 10))
            vars_[label] = e
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(6, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)

        def do_crop():
            try:
                vals = [float(vars_[k].get() or 0) for k in ("Left", "Top", "Right", "Bottom")]
            except ValueError:
                messagebox.showerror("Crop", "All values must be numbers.", parent=dlg)
                return
            pages = list(self.tool.selection) if self.tool.selection else None
            dlg.destroy()
            try:
                self.tool.crop_pages(pages, left=vals[0], top=vals[1], right=vals[2], bottom=vals[3])
            except ValueError as e:
                messagebox.showerror("Crop", str(e), parent=self.root)
                return
            self._refresh_preview()

        HoverButton(btn_row, "Crop", command=do_crop, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    def _parse_range(self, s, total):
        idx = []
        for part in s.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-")
                a, b = int(a), int(b)
                if a < 1 or b > total or a > b:
                    raise ValueError(f"Range {part} is invalid (max {total}).")
                idx.extend(range(a - 1, b))
            else:
                v = int(part)
                if v < 1 or v > total:
                    raise ValueError(f"Page {v} is invalid (max {total}).")
                idx.append(v - 1)
        if not idx:
            raise ValueError("No pages selected.")
        return idx
