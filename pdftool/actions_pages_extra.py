"""Page composition actions: extract, chunked split, N-up, booklet, blank-page
cleanup, margin trimming and a document summary.

Every PDF operation runs on a worker thread through :func:`pdftool.jobs.run_job`
and works on a byte snapshot, so the UI never blocks and the live document is
never touched by two threads at once.
"""
import os

import tkinter as tk
from tkinter import messagebox

from .theme import (
    BG, SURFACE, BORDER, TEXT, TEXT_MUTED, ACCENT, FONT, FONT_SMALL, FONT_BOLD,
)
from .widgets import HoverButton, _safe_grab
from .dialogs import native_save_file, native_save_file_any, native_open_dir
from .jobs import progress_bridge, run_job
from pdf_ops_pages import (
    parse_ranges, validate_range_spec, extract_pages, split_every, split_ranges,
    nup, booklet, blank_pages as find_blank_pages, auto_crop, delete_pages,
)


def unique_path(folder, stem, ext, taken=None):
    """``folder/stem.ext``, suffixed ``(2)``, ``(3)``... until unused."""
    taken = taken or set()
    candidate = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(candidate) or candidate in taken:
        candidate = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    taken.add(candidate)
    return candidate


def write_parts(folder, stem, parts):
    """Write ``[(label, bytes)]`` into ``folder``; returns the written paths."""
    os.makedirs(folder, exist_ok=True)
    written, taken = [], set()
    for label, data in parts:
        path = unique_path(folder, f"{stem}_{label}", ".pdf", taken)
        with open(path, "wb") as fh:
            fh.write(data)
        written.append(path)
    return written


class PagesExtraActions:
    """Mixin - combined into PDFToolApp via multiple inheritance."""

    # ---------- registry ----------

    def sidebar_entries(self):
        return [("PAGE TOOLS", [
            ("Extract Pages", "extract_pages_ui"),
            ("Split into Chunks", "split_chunks_ui"),
            ("N-up Imposition", "nup_ui"),
            ("Booklet (duplex)", "booklet_ui"),
            ("Find & Remove Blank Pages", "blank_pages_ui"),
            ("Auto-trim Margins", "trim_margins_ui"),
            ("Document Summary", "stats_ui"),
        ])]

    def shortcuts(self):
        return [("<Control-Shift-e>", "extract_pages_ui")]

    def standalone_methods(self):
        return set()

    # ---------- helpers ----------

    def _doc_stem(self):
        path = self.tool.path or "document"
        return os.path.splitext(os.path.basename(path))[0]

    def _doc_dir(self):
        return os.path.dirname(self.tool.path) if self.tool.path else ""

    def _selection_spec(self):
        """Current selection as a range string, or "" when nothing is selected."""
        sel = self.tool.selection
        if not sel:
            return ""
        ranges, start, prev = [], None, None
        for i in sel:
            if start is None:
                start = prev = i
            elif i == prev + 1:
                prev = i
            else:
                ranges.append((start, prev))
                start = prev = i
        ranges.append((start, prev))
        return ",".join(str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in ranges)

    def _dialog(self, title):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        _safe_grab(dlg)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        return dlg

    def _field_row(self, dlg, label, value="", width=34):
        row = tk.Frame(dlg, bg=BG)
        row.pack(fill=tk.X, padx=16, pady=(6, 0))
        tk.Label(row, text=label, font=FONT_SMALL, bg=BG, fg=TEXT,
                 anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=value)
        entry = tk.Entry(row, textvariable=var, width=width, font=FONT, relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=BORDER)
        entry.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        return var, entry

    def _path_row(self, dlg, label, title, initial, any_ext=False):
        row = tk.Frame(dlg, bg=BG)
        row.pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(row, text=label, font=FONT_SMALL, bg=BG, fg=TEXT).pack(anchor="w")
        inner = tk.Frame(row, bg=BG)
        inner.pack(fill=tk.X, pady=(2, 0))
        var = tk.StringVar(value="")
        tk.Entry(inner, textvariable=var, font=FONT, relief=tk.FLAT, state="readonly",
                 highlightthickness=1, highlightbackground=BORDER).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        def browse():
            picked = (native_save_file_any(title, initial) if any_ext
                      else native_save_file(title, initial))
            if picked:
                var.set(picked)

        HoverButton(inner, "Browse", command=browse, small=True).pack(side=tk.LEFT, padx=(6, 0))
        return var

    def _button_row(self, dlg, primary_text, primary_cmd, width_note=None):
        row = tk.Frame(dlg, bg=BG)
        row.pack(pady=(14, 14))
        HoverButton(row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(row, primary_text, command=primary_cmd, primary=True,
                    small=True).pack(side=tk.LEFT, padx=6)
        if width_note:
            tk.Label(dlg, text=width_note, font=FONT_SMALL, bg=BG, fg=TEXT_MUTED,
                     wraplength=360, justify=tk.LEFT).pack(padx=16, pady=(6, 0), anchor="w")
        return row

    # ---------- extract ----------

    def extract_pages_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Extract Pages")
        tk.Label(dlg, text="Pages to extract (e.g. 1,3,5-7):", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        spec_var, spec_entry = self._field_row(dlg, "", self._selection_spec(), width=44)
        out_var = self._path_row(dlg, "Save extracted pages to:", "Save extracted pages",
                                 f"{self._doc_stem()}_extract.pdf")

        def start(*_):
            spec = spec_var.get().strip()
            total = self.tool.page_count
            if not validate_range_spec(spec, total):
                spec_entry.config(highlightbackground="#c0392b", highlightcolor="#c0392b")
                self.status("Invalid page range - use 1,3,5-7 within 1-%d." % total, "error")
                return
            out = out_var.get().strip()
            if not out:
                messagebox.showerror("Extract Pages", "Choose an output file first.", parent=dlg)
                return
            ranges = parse_ranges(spec, total)
            indexes = [i for a, b in ranges for i in range(a, b + 1)]
            snap = self.tool.snapshot_bytes()
            label = f"{len(indexes)} pages"

            def work(job):
                job.progress(label, 0.4)
                return extract_pages(snap, indexes)

            dlg.destroy()

            def done(data):
                try:
                    with open(out, "wb") as fh:
                        fh.write(data)
                except OSError as exc:
                    messagebox.showerror("Extract Pages", f"Could not write the file:\n{exc}",
                                         parent=self.root)
                    return
                self.status(f"Extracted {len(indexes)} pages to {out}")
                messagebox.showinfo("Extract Pages", f"Saved {len(indexes)} pages to\n{out}",
                                    parent=self.root)

            run_job(self, "Extracting pages", work, on_done=done, name="extract-pages")

        self._button_row(dlg, "Extract", start)
        dlg.bind("<Return>", start)
        spec_entry.focus_set()

    # ---------- split ----------

    def split_chunks_ui(self):
        if not self._require_doc():
            return
        total = self.tool.page_count
        dlg = self._dialog("Split PDF")
        mode = tk.StringVar(value="every")
        tk.Label(dlg, text=f"Split {total} pages into separate files", font=FONT_BOLD,
                 bg=BG, fg=TEXT).pack(anchor="w", padx=16, pady=(14, 4))

        row_n = tk.Frame(dlg, bg=BG)
        row_n.pack(fill=tk.X, padx=16, pady=(2, 2))
        tk.Radiobutton(row_n, text="Every", variable=mode, value="every", bg=BG, fg=TEXT,
                       font=FONT, activebackground=BG, selectcolor=SURFACE).pack(side=tk.LEFT)
        n_var = tk.StringVar(value="10")
        tk.Entry(row_n, textvariable=n_var, width=6, font=FONT, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER).pack(side=tk.LEFT, padx=6)
        tk.Label(row_n, text="pages", font=FONT, bg=BG, fg=TEXT).pack(side=tk.LEFT)

        row_r = tk.Frame(dlg, bg=BG)
        row_r.pack(fill=tk.X, padx=16, pady=(2, 2))
        tk.Radiobutton(row_r, text="At page ranges", variable=mode, value="ranges", bg=BG,
                       fg=TEXT, font=FONT, activebackground=BG,
                       selectcolor=SURFACE).pack(side=tk.LEFT)
        ranges_var = tk.StringVar(value="1-3,7,10-%d" % total)
        tk.Entry(row_r, textvariable=ranges_var, width=20, font=FONT, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER).pack(side=tk.LEFT, padx=6)

        out_row = tk.Frame(dlg, bg=BG)
        out_row.pack(fill=tk.X, padx=16, pady=(10, 0))
        tk.Label(out_row, text="Output folder:", font=FONT_SMALL, bg=BG,
                 fg=TEXT).pack(anchor="w")
        inner = tk.Frame(out_row, bg=BG)
        inner.pack(fill=tk.X, pady=(2, 0))
        out_var = tk.StringVar(value=self._doc_dir())
        tk.Entry(inner, textvariable=out_var, font=FONT, relief=tk.FLAT, state="readonly",
                 highlightthickness=1, highlightbackground=BORDER).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        def browse():
            picked = native_open_dir("Choose the output folder")
            if picked:
                out_var.set(picked)

        HoverButton(inner, "Browse", command=browse, small=True).pack(side=tk.LEFT, padx=(6, 0))

        def start(*_):
            folder = out_var.get().strip()
            if not folder:
                messagebox.showerror("Split PDF", "Choose an output folder.", parent=dlg)
                return
            snap = self.tool.snapshot_bytes()
            stem = self._doc_stem()
            if mode.get() == "every":
                try:
                    n = int(n_var.get())
                    if n < 1:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("Split PDF", "Pages per file must be a positive number.",
                                         parent=dlg)
                    return

                def work(job):
                    job.progress("Splitting pages", None)
                    return split_every(snap, n)
            else:
                spec = ranges_var.get().strip()
                if not validate_range_spec(spec, total):
                    messagebox.showerror(
                        "Split PDF", "Invalid range - use e.g. 1-3,7 within 1-%d." % total,
                        parent=dlg)
                    return
                wanted = parse_ranges(spec, total)
                dlg.destroy()

                def work(job):
                    job.progress("Splitting ranges", None)
                    return split_ranges(snap, wanted)

            dlg.destroy()
            mode_label = mode.get()

            def done(parts):
                try:
                    paths = write_parts(folder, stem, parts)
                except OSError as exc:
                    messagebox.showerror("Split PDF", f"Could not write the files:\n{exc}",
                                         parent=self.root)
                    return
                preview = "\n".join(os.path.basename(p) for p in paths[:5])
                more = "" if len(paths) <= 5 else f"\n... and {len(paths) - 5} more"
                self.status(f"Split into {len(paths)} files in {folder}")
                messagebox.showinfo("Split PDF",
                                    f"Wrote {len(paths)} files ({mode_label}) to\n{folder}\n\n"
                                    f"{preview}{more}", parent=self.root)

            run_job(self, "Splitting PDF", work, on_done=done, name="split-chunks")

        self._button_row(dlg, "Split", start, width_note=(
            "Existing files are never overwritten: new names get a (2), (3)... suffix."))
        dlg.bind("<Return>", start)

    # ---------- imposition ----------

    def nup_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("N-up Imposition")
        tk.Label(dlg, text="Pages per sheet", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        per_sheet = tk.IntVar(value=4)
        row = tk.Frame(dlg, bg=BG)
        row.pack(padx=16, anchor="w")
        for val in (2, 4, 6, 9, 16):
            tk.Radiobutton(row, text=str(val), variable=per_sheet, value=val, bg=BG, fg=TEXT,
                           font=FONT, activebackground=BG,
                           selectcolor=SURFACE).pack(side=tk.LEFT, padx=(0, 8))

        margin_var, _ = self._field_row(dlg, "Margin (pt):", "18", width=8)
        gutter_var, _ = self._field_row(dlg, "Gutter (pt):", "8", width=8)
        border_var = tk.BooleanVar(value=False)
        tk.Checkbutton(dlg, text="Draw cell borders", variable=border_var, bg=BG, fg=TEXT,
                       font=FONT_SMALL, activebackground=BG, selectcolor=SURFACE).pack(
            anchor="w", padx=16, pady=(8, 0))
        out_var = self._path_row(dlg, "Save N-up PDF to:", "Save N-up PDF",
                                 f"{self._doc_stem()}_nup.pdf")

        def start(*_):
            out = out_var.get().strip()
            if not out:
                messagebox.showerror("N-up", "Choose an output file.", parent=dlg)
                return
            try:
                margin = max(0.0, float(margin_var.get() or 0))
                gutter = max(0.0, float(gutter_var.get() or 0))
            except ValueError:
                messagebox.showerror("N-up", "Margin and gutter must be numbers.", parent=dlg)
                return
            snap, per = self.tool.snapshot_bytes(), per_sheet.get()
            total = self.tool.page_count
            dlg.destroy()

            def work(job):
                return nup(snap, per_sheet=per, margin=margin, gutter=gutter,
                           border=border_var.get(),
                           progress=progress_bridge(job, "Laying out sheets"))

            def done(data):
                try:
                    with open(out, "wb") as fh:
                        fh.write(data)
                except OSError as exc:
                    messagebox.showerror("N-up", f"Could not write the file:\n{exc}",
                                         parent=self.root)
                    return
                sheets = (total + per - 1) // per
                self.status(f"Created {sheets} N-up sheets")
                messagebox.showinfo("N-up", f"Wrote {sheets} sheets to\n{out}", parent=self.root)

            run_job(self, "Creating N-up PDF", work, on_done=done, name="nup")

        self._button_row(dlg, "Create", start)
        dlg.bind("<Return>", start)

    def booklet_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Booklet (duplex)")
        tk.Label(dlg, text="Print-ready saddle-stitch booklet", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(dlg, text=("Pages are reordered two-per-side for double-sided printing and "
                            "padded with blanks to a multiple of four. Print double-sided "
                            "(flip on long edge), then fold in half."),
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, wraplength=380,
                 justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 6))
        margin_var, _ = self._field_row(dlg, "Margin (pt):", "18", width=8)
        gutter_var, _ = self._field_row(dlg, "Gutter (pt):", "12", width=8)
        out_var = self._path_row(dlg, "Save booklet PDF to:", "Save booklet PDF",
                                 f"{self._doc_stem()}_booklet.pdf")

        def start(*_):
            out = out_var.get().strip()
            if not out:
                messagebox.showerror("Booklet", "Choose an output file.", parent=dlg)
                return
            try:
                margin = max(0.0, float(margin_var.get() or 0))
                gutter = max(0.0, float(gutter_var.get() or 0))
            except ValueError:
                messagebox.showerror("Booklet", "Margin and gutter must be numbers.", parent=dlg)
                return
            snap = self.tool.snapshot_bytes()
            dlg.destroy()

            def work(job):
                return booklet(snap, margin=margin, gutter=gutter,
                               progress=progress_bridge(job, "Imposing booklet"))

            def done(data):
                try:
                    with open(out, "wb") as fh:
                        fh.write(data)
                except OSError as exc:
                    messagebox.showerror("Booklet", f"Could not write the file:\n{exc}",
                                         parent=self.root)
                    return
                self.status(f"Booklet written to {out}")
                messagebox.showinfo("Booklet", f"Booklet PDF written to\n{out}", parent=self.root)

            run_job(self, "Creating booklet", work, on_done=done, name="booklet")

        self._button_row(dlg, "Create", start)
        dlg.bind("<Return>", start)

    # ---------- blank pages ----------

    def blank_pages_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Find & Remove Blank Pages")
        tk.Label(dlg, text="Scanning for blank pages...", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(dlg, text=("A page counts as blank when its ink covers less than 0.08% of "
                            "the sheet: scanned pages with a grey background are detected "
                            "too, because the dominant colour is treated as paper."),
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, wraplength=380,
                 justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 8))

        frame = tk.Frame(dlg, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=16)
        listbox = tk.Listbox(frame, height=9, width=42, font=FONT_SMALL, bg=SURFACE, fg=TEXT,
                            selectbackground=ACCENT, selectforeground="#ffffff",
                            relief=tk.FLAT, highlightthickness=1,
                            highlightbackground=BORDER, activestyle="none")
        sb = tk.Scrollbar(frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        count = tk.Label(dlg, text="", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED)
        count.pack(anchor="w", padx=16, pady=(6, 0))

        found = {"pages": []}

        def apply_delete(indexes):
            if not indexes:
                return
            if not messagebox.askyesno("Remove blank pages",
                                       f"Delete {len(indexes)} page(s)?", parent=dlg):
                return
            snap = self.tool.snapshot_bytes()
            dlg.destroy()
            self._apply_pdf_bytes(delete_pages(snap, indexes), "Remove blank pages")
            self.status(f"Removed {len(indexes)} blank page(s)")

        def delete_selected():
            apply_delete([found["pages"][i] for i in listbox.curselection()])

        def delete_all():
            apply_delete(list(found["pages"]))

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(10, 14))
        HoverButton(btn_row, "Close", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Delete all", command=delete_all, danger=True,
                    small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Delete selected", command=delete_selected, primary=True,
                    small=True).pack(side=tk.LEFT, padx=6)

        snap = self.tool.snapshot_bytes()

        def work(job):
            return find_blank_pages(snap, progress=progress_bridge(job, "Scanning pages"))

        def done(pages):
            found["pages"] = list(pages)
            listbox.delete(0, tk.END)
            for p in pages:
                listbox.insert(tk.END, f"Page {p + 1}")
            count.config(text=(f"{len(pages)} blank page(s) found." if pages
                               else "No blank pages found."))
            self.status(f"{len(pages)} blank page(s) detected")

        run_job(self, "Finding blank pages", work, on_done=done, name="blank-pages")

    # ---------- trim ----------

    def trim_margins_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Auto-trim Margins")
        tk.Label(dlg, text="Trim white margins on every page", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(dlg, text=("Each page is measured by rendering it grey-scale and finding the "
                            "ink bounding box. Pages that are already full-bleed are left "
                            "alone."),
                 font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, wraplength=380,
                 justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 6))
        pad_var, _ = self._field_row(dlg, "Safety padding (%):", "1.0", width=8)

        def start(*_):
            try:
                pct = float(pad_var.get() or 0)
            except ValueError:
                messagebox.showerror("Auto-trim", "Padding must be a number.", parent=dlg)
                return
            pct = min(max(pct, 0.0), 10.0)
            snap = self.tool.snapshot_bytes()
            dlg.destroy()

            def work(job):
                return auto_crop(snap, padding=pct / 100.0,
                                 progress=progress_bridge(job, "Measuring margins"))

            def done(result):
                data, changed = result
                if not changed:
                    messagebox.showinfo("Auto-trim", "Nothing to trim: the pages are already "
                                                     "full-bleed.", parent=self.root)
                    self.status("No margins to trim")
                    return
                self._apply_pdf_bytes(data, "Auto-trim margins")
                self.status(f"Trimmed margins on {changed} page(s)")
                messagebox.showinfo("Auto-trim", f"Trimmed margins on {changed} page(s).",
                                    parent=self.root)

            run_job(self, "Trimming margins", work, on_done=done, name="trim-margins")

        self._button_row(dlg, "Trim", start)
        dlg.bind("<Return>", start)

    # ---------- summary ----------

    def stats_ui(self):
        if not self._require_doc():
            return
        w, h = self.tool.page_size(0)
        size = 0
        if self.tool.path and os.path.isfile(self.tool.path):
            size = os.path.getsize(self.tool.path)
        rows = [
            ("Pages", str(self.tool.page_count)),
            ("Selected", str(len(self.tool.selection))),
            ("Page size", f"{w:.0f} x {h:.0f} pt  ({w * 25.4 / 72:.0f} x {h * 25.4 / 72:.0f} mm)"),
            ("Encrypted", "yes" if self.tool.was_encrypted else "no"),
            ("On disk", f"{size / 1024.0:.1f} KB" if size else "-"),
            ("Unsaved changes", "yes" if self.tool.dirty else "no"),
            ("Undo steps", str(len(getattr(self.tool, "_history", [])))),
        ]
        dlg = self._dialog("Document Summary")
        tk.Label(dlg, text=os.path.basename(self.tool.path or "Untitled"), font=FONT_BOLD,
                 bg=BG, fg=TEXT).pack(anchor="w", padx=16, pady=(14, 6))
        grid = tk.Frame(dlg, bg=BG)
        grid.pack(padx=16, anchor="w")
        for i, (key, value) in enumerate(rows):
            tk.Label(grid, text=key, font=FONT_SMALL, bg=BG, fg=TEXT_MUTED,
                     anchor="w").grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(grid, text=value, font=FONT, bg=BG, fg=TEXT,
                     anchor="w").grid(row=i, column=1, sticky="w", padx=(16, 0), pady=1)
        HoverButton(dlg, "Close", command=dlg.destroy, small=True).pack(pady=(14, 14))