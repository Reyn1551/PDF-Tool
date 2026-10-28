"""Redaction, document sanitising and attachment management.

All processing is bytes-in / bytes-out on a worker thread; results are applied
through ``app._apply_pdf_bytes`` so they land on the undo stack.
"""
import os

import tkinter as tk
from tkinter import messagebox, simpledialog

from .theme import (
    BG, SURFACE, BORDER, TEXT, TEXT_MUTED, ACCENT, DANGER, FONT, FONT_SMALL, FONT_BOLD,
)
from .widgets import HoverButton, _safe_grab
from .dialogs import native_open_file, native_open_dir
from .jobs import run_job
from pdf_ops_security import (
    find_matches, redact, inspect, sanitize, list_attachments, add_attachment,
    delete_attachment, extract_attachment,
)


def parse_page_spec(spec, total):
    """``"1-3,7"`` -> ``[0,1,2,6]``; ``""`` -> ``None`` (meaning: all pages).

    Raises :class:`ValueError` for malformed input or out-of-range pages.
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2:
                raise ValueError(f"Invalid range '{part}'.")
            try:
                a, b = int(bits[0]), int(bits[1])
            except ValueError:
                raise ValueError(f"Invalid range '{part}'.")
            if a < 1 or b > total or a > b:
                raise ValueError(f"Range '{part}' is outside 1-{total}.")
            out.extend(range(a - 1, b))
        else:
            try:
                value = int(part)
            except ValueError:
                raise ValueError(f"Invalid page '{part}'.")
            if value < 1 or value > total:
                raise ValueError(f"Page {value} is outside 1-{total}.")
            out.append(value - 1)
    if not out:
        raise ValueError("No pages selected.")
    return sorted(set(out))


def _fmt_count(n):
    return f"{n:,}"


class RedactActions:
    """Mixin - combined into PDFToolApp via multiple inheritance."""

    # ---------- registry ----------

    def sidebar_entries(self):
        return [("REDACT & SANITIZE", [
            ("Redact Text (Permanent)", "redact_ui"),
            ("Sanitize Document", "sanitize_ui"),
            ("Attachments", "attachments_ui"),
        ])]

    def shortcuts(self):
        return [("<Control-Shift-r>", "redact_ui")]

    def standalone_methods(self):
        return set()

    # ---------- helpers ----------

    def _dialog(self, title):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.configure(bg=BG)
        _safe_grab(dlg)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        return dlg

    def _hint(self, dlg, text):
        tk.Label(dlg, text=text, font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, wraplength=420,
                 justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 8))

    # ---------- redact ----------

    def redact_ui(self):
        if not self._require_doc():
            return
        total = self.tool.page_count
        dlg = self._dialog("Redact Text")
        tk.Label(dlg, text="Text to redact", font=FONT_BOLD, bg=BG, fg=TEXT).pack(
            anchor="w", padx=16, pady=(14, 2))
        query_var = tk.StringVar()
        query = tk.Entry(dlg, textvariable=query_var, width=46, font=FONT, relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=BORDER)
        query.pack(fill=tk.X, padx=16, pady=(0, 6))

        opts = tk.Frame(dlg, bg=BG)
        opts.pack(fill=tk.X, padx=16)
        case_var = tk.BooleanVar(value=False)
        word_var = tk.BooleanVar(value=False)
        regex_var = tk.BooleanVar(value=False)
        for text, var in (("Match case", case_var), ("Whole word", word_var),
                          ("Regex", regex_var)):
            tk.Checkbutton(opts, text=text, variable=var, bg=BG, fg=TEXT, font=FONT_SMALL,
                           activebackground=BG, selectcolor=SURFACE).pack(side=tk.LEFT,
                                                                         padx=(0, 10))

        page_row = tk.Frame(dlg, bg=BG)
        page_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(page_row, text="Pages (blank = all):", font=FONT_SMALL, bg=BG,
                 fg=TEXT_MUTED).pack(side=tk.LEFT)
        page_var = tk.StringVar()
        page_entry = tk.Entry(page_row, textvariable=page_var, width=18, font=FONT_SMALL,
                             relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        page_entry.pack(side=tk.LEFT, padx=(8, 0))

        self._hint(dlg, ("Matches are permanently removed from the content stream: the text "
                         "cannot be selected, copied or recovered after saving. Always keep a "
                         "copy of the original."))

        list_frame = tk.Frame(dlg, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16)
        hits_box = tk.Listbox(list_frame, height=8, width=56, font=FONT_SMALL, bg=SURFACE,
                             fg=TEXT, selectbackground=ACCENT, selectforeground="#ffffff",
                             relief=tk.FLAT, highlightthickness=1,
                             highlightbackground=BORDER, activestyle="none")
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=hits_box.yview)
        hits_box.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        hits_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        counter = tk.Label(dlg, text="No search run yet.", font=FONT_SMALL, bg=BG,
                           fg=TEXT_MUTED)
        counter.pack(anchor="w", padx=16, pady=(6, 0))

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(10, 16))
        HoverButton(btn_row, "Close", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        find_btn = HoverButton(btn_row, "Find matches", command=lambda: do_find(), small=True)
        find_btn.pack(side=tk.LEFT, padx=6)
        redact_btn = HoverButton(btn_row, "Redact matches", command=lambda: do_redact(),
                                 primary=True, small=True)
        redact_btn.pack(side=tk.LEFT, padx=6)
        redact_btn.set_enabled(False)

        state = {"hits": [], "pages": None, "pending": None}

        def read_input(quiet=False):
            needle = query_var.get().strip()
            if not needle:
                if not quiet:
                    messagebox.showerror("Redact", "Enter the text to redact.", parent=dlg)
                return None
            try:
                pages = parse_page_spec(page_var.get(), total)
            except ValueError as exc:
                page_entry.config(highlightbackground=DANGER, highlightcolor=DANGER)
                if not quiet:
                    messagebox.showerror("Redact", str(exc), parent=dlg)
                page_entry.focus_set()
                return None
            page_entry.config(highlightbackground=BORDER, highlightcolor=BORDER)
            state["pages"] = pages
            if regex_var.get():
                return {"pattern": needle, "terms": ()}
            return {"terms": (needle,), "pattern": None}

        def clear_results():
            state["hits"] = []
            hits_box.delete(0, tk.END)
            counter.config(text="Type the text to redact, then press Enter.", fg=TEXT_MUTED)
            redact_btn.set_enabled(False)

        def do_find(*_, quiet=False):
            args = read_input(quiet=quiet)
            if args is None:
                if quiet and not query_var.get().strip():
                    clear_results()
                    find_job = state.get("job")
                    if find_job is not None:
                        find_job.cancel()
                return
            snap = self.tool.snapshot_bytes()
            kwargs = dict(args, pages=state["pages"], case_sensitive=case_var.get(),
                          whole_word=word_var.get())
            counter.config(text="Searching...")

            def work(job):
                return find_matches(snap, **kwargs)

            def done(hits):
                state["hits"] = hits
                hits_box.delete(0, tk.END)
                for hit in hits[:300]:
                    snippet = " ".join(hit["text"].split())
                    if len(snippet) > 60:
                        snippet = snippet[:57] + "..."
                    hits_box.insert(tk.END, f"Page {hit['page'] + 1}:  {snippet}")
                if len(hits) > 300:
                    hits_box.insert(tk.END, f"... {len(hits) - 300} more matches not listed")
                counter.config(text=f"{_fmt_count(len(hits))} match(es) found.")
                redact_btn.set_enabled(bool(hits))
                self.status(f"{len(hits)} redaction target(s) found")
                state["job"] = None

            def failed(exc):
                counter.config(text="Search failed.")
                messagebox.showerror("Redact", str(exc), parent=self.root)

            state["job"] = run_job(self, "Finding redaction targets", work, on_done=done,
                                   on_error=failed, name="redact-find")

        def schedule_find(*_):
            """Debounced live search so the match list follows the typing."""
            if state["pending"] is not None:
                try:
                    dlg.after_cancel(state["pending"])
                except tk.TclError:
                    pass
            state["pending"] = dlg.after(350, lambda: (state.__setitem__("pending", None),
                                                       do_find(quiet=True)))

        def do_redact():
            if not state["hits"]:
                return
            args = read_input()
            if args is None:
                return
            count = len(state["hits"])
            if not messagebox.askyesno(
                    "Confirm Redaction",
                    f"Permanently remove {_fmt_count(count)} match(es)?\n\n"
                    "The text is deleted from the content stream, so it cannot be copied "
                    "back out afterwards.", icon="warning", parent=dlg):
                return
            snap = self.tool.snapshot_bytes()
            kwargs = dict(args, pages=state["pages"], case_sensitive=case_var.get(),
                          whole_word=word_var.get())
            dlg.destroy()

            def work(job):
                return redact(snap, progress=job.progress, **kwargs)

            def done(result):
                data, hits = result
                self._apply_pdf_bytes(data, "Redact text")
                self.status(f"Redacted {_fmt_count(len(hits))} match(es)")

            run_job(self, "Applying redactions", work, on_done=done, name="redact-apply")

        dlg.bind("<Return>", lambda *_: do_find())
        dlg.bind("<Destroy>", lambda *_: state["pending"] is not None
                 and dlg.after_cancel(state["pending"]))
        for var in (query_var, page_var):
            var.trace_add("write", schedule_find)
        for var in (case_var, word_var, regex_var):
            var.trace_add("write", schedule_find)
        query.focus_set()
        clear_results()

    # ---------- sanitize ----------

    def sanitize_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Sanitize Document")
        tk.Label(dlg, text="Strip identifying and executable payloads", font=FONT_BOLD,
                 bg=BG, fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        self._hint(dlg, ("Hidden text, JavaScript, embedded files and metadata are removed. "
                         "The report below shows exactly what the file currently carries."))
        report_frame = tk.Frame(dlg, bg=BG)
        report_frame.pack(fill=tk.BOTH, expand=True, padx=16)
        report_box = tk.Listbox(report_frame, height=10, width=58, font=FONT_SMALL,
                               bg=SURFACE, fg=TEXT, relief=tk.FLAT, highlightthickness=1,
                               highlightbackground=BORDER, activestyle="none")
        sb = tk.Scrollbar(report_frame, orient=tk.VERTICAL, command=report_box.yview)
        report_box.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        report_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        flags = {}
        flag_row = tk.Frame(dlg, bg=BG)
        flag_row.pack(fill=tk.X, padx=16, pady=(8, 0))
        for key, text, default in (
            ("metadata", "Metadata", True),
            ("attachments", "Attachments", True),
            ("embedded_files", "Embedded files", True),
            ("javascript", "JavaScript", True),
            ("hidden_text", "Hidden text / OCR layer", True),
            ("annotations", "Annotations", False),
        ):
            var = tk.BooleanVar(value=default)
            flags[key] = var
            tk.Checkbutton(flag_row, text=text, variable=var, bg=BG, fg=TEXT,
                           font=FONT_SMALL, activebackground=BG,
                           selectcolor=SURFACE).pack(side=tk.LEFT, padx=(0, 8))

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 16))

        def refresh():
            snap = self.tool.snapshot_bytes()

            def work(job):
                return inspect(snap)

            def done(info):
                report_box.delete(0, tk.END)
                meta = info.get("metadata") or {}
                if meta:
                    for key, value in meta.items():
                        text = " ".join(str(value).split())
                        if len(text) > 52:
                            text = text[:49] + "..."
                        report_box.insert(tk.END, f"metadata  {key} = {text}")
                else:
                    report_box.insert(tk.END, "metadata  (empty)")
                report_box.insert(tk.END, f"attachments  {len(info.get('attachments') or [])}")
                report_box.insert(tk.END, f"annotations  {info.get('annotations', 0)}")
                report_box.insert(tk.END, f"javascript refs  {info.get('javascript_refs', 0)}")
                report_box.insert(tk.END, f"pages  {info.get('pages', 0)}")
                report_box.insert(
                    tk.END, "encrypted  " + ("yes" if info.get("encrypted") else "no"))

            run_job(self, "Inspecting document", work, on_done=done, name="sanitize-inspect")

        def start():
            selected = {k: v.get() for k, v in flags.items()}
            if not any(selected.values()):
                messagebox.showinfo("Sanitize", "Select at least one item to strip.",
                                    parent=dlg)
                return
            if not messagebox.askyesno(
                    "Sanitize Document",
                    "Removing metadata, attachments or hidden text cannot be undone after "
                    "saving the result. Continue?", icon="warning", parent=dlg):
                return
            snap = self.tool.snapshot_bytes()
            dlg.destroy()

            def work(job):
                return sanitize(snap, progress=job.progress, **selected)

            def done(result):
                data, report = result
                self._apply_pdf_bytes(data, "Sanitize document")
                removed = []
                if (report.get("metadata") or {}) and selected["metadata"]:
                    removed.append(f"{len(report['metadata'])} metadata fields")
                if report.get("attachments") and selected["attachments"]:
                    removed.append(f"{len(report['attachments'])} attachments")
                if report.get("annotations"):
                    removed.append(f"{report['annotations']} annotations")
                summary = ", ".join(removed) or "no removable items"
                self.status(f"Sanitized: {summary}")
                messagebox.showinfo("Sanitize", f"Document sanitized ({summary}).",
                                    parent=self.root)

            run_job(self, "Sanitizing document", work, on_done=done, name="sanitize")

        HoverButton(btn_row, "Close", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Refresh report", command=refresh, small=True).pack(side=tk.LEFT,
                                                                                padx=6)
        HoverButton(btn_row, "Shred now", command=start, danger=True,
                    small=True).pack(side=tk.LEFT, padx=6)
        refresh()

    # ---------- attachments ----------

    def attachments_ui(self):
        if not self._require_doc():
            return
        dlg = self._dialog("Attachments")
        tk.Label(dlg, text="Files embedded inside this PDF", font=FONT_BOLD, bg=BG,
                 fg=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        self._hint(dlg, ("Attachments travel with the PDF. Use Extract to pull one out, or Add "
                         "to wrap any file (invoices, XML, images) into the document."))

        frame = tk.Frame(dlg, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=16)
        box = tk.Listbox(frame, height=9, width=54, font=FONT_SMALL, bg=SURFACE, fg=TEXT,
                         selectbackground=ACCENT, selectforeground="#ffffff", relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=BORDER,
                         selectmode=tk.EXTENDED, activestyle="none")
        sb = tk.Scrollbar(frame, orient=tk.VERTICAL, command=box.yview)
        box.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        names = []

        def selected_names():
            return [names[i] for i in box.curselection()]

        def render(entries):
            names.clear()
            box.delete(0, tk.END)
            for entry in entries:
                names.append(entry["name"])
                size = entry.get("size") or 0
                desc = (entry.get("description") or "").strip()
                label = f"{entry['name']}  ({size / 1024.0:.1f} KB)"
                if desc:
                    label += f"  - {desc}"
                box.insert(tk.END, label)
            if not entries:
                box.insert(tk.END, "(no attachments)")

        def reload(entries=None):
            if entries is not None:
                render(entries)
                return
            snap = self.tool.snapshot_bytes()

            def work(job):
                return list_attachments(snap)

            run_job(self, "Listing attachments", work, on_done=render, name="attachments-list")

        def add():
            path = native_open_file("Choose a file to attach", pdf_only=False)
            if not path:
                return
            desc = simpledialog.askstring("Attachment description",
                                          "Optional description:", parent=dlg) or ""
            snap = self.tool.snapshot_bytes()

            def work(job):
                return add_attachment(snap, path, description=desc)

            def done(result):
                data, name = result
                self._apply_pdf_bytes(data, f"Attach {name}")
                self.status(f"Attached {name}")
                reload()

            run_job(self, "Adding attachment", work, on_done=done, name="attachments-add")

        def extract():
            picked = selected_names()
            if not picked:
                messagebox.showinfo("Attachments", "Select an attachment first.", parent=dlg)
                return
            folder = native_open_dir("Choose a folder for the extracted file")
            if not folder:
                return
            snap = self.tool.snapshot_bytes()

            def work(job):
                written = []
                for i, name in enumerate(picked, 1):
                    job.progress(f"Extracting {name}", i / float(len(picked)))
                    target = os.path.join(folder, os.path.basename(name))
                    written.append(extract_attachment(snap, name, target))
                return written

            def done(paths):
                self.status(f"Extracted {len(paths)} attachment(s)")
                messagebox.showinfo("Attachments",
                                    "Extracted:\n" + "\n".join(paths), parent=self.root)

            run_job(self, "Extracting attachments", work, on_done=done,
                    name="attachments-extract")

        def delete():
            picked = selected_names()
            if not picked:
                messagebox.showinfo("Attachments", "Select an attachment first.", parent=dlg)
                return
            if not messagebox.askyesno("Attachments",
                                       "Remove %d attachment(s) from the PDF?" % len(picked),
                                       parent=dlg):
                return
            snap = self.tool.snapshot_bytes()

            def work(job):
                data = snap
                for i, name in enumerate(picked, 1):
                    job.progress(f"Removing {name}", i / float(len(picked)))
                    data = delete_attachment(data, name)
                return data

            def done(data):
                self._apply_pdf_bytes(data, "Remove attachments")
                self.status(f"Removed {len(picked)} attachment(s)")
                reload()

            run_job(self, "Removing attachments", work, on_done=done, name="attachments-del")

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 16))
        HoverButton(btn_row, "Close", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Delete", command=delete, danger=True,
                    small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Extract", command=extract, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Add file", command=add, primary=True,
                    small=True).pack(side=tk.LEFT, padx=6)
        reload()