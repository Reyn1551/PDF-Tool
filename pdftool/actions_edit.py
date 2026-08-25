"""Digital text & free-text editor for scanned PDFs, image stamps, metadata."""
import os

import tkinter as tk
from tkinter import messagebox

import fitz

from .theme import BG, SURFACE, BORDER, TEXT, TEXT_MUTED, ACCENT, ACCENT_SOFT, DANGER, FONT, FONT_SMALL, FONT_BOLD
from .widgets import HoverButton, _safe_grab


class EditActions:
    """Mixin — combined into PDFToolApp via multiple inheritance."""

    def edit_text(self):
        if not self._require_doc():
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit & Add Text")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        dlg.geometry("980x680")
        dlg.minsize(860, 580)

        # State dialog
        dlg.current_page = self.tool.selection[0] if self.tool.selection else 0
        dlg.spans = []
        dlg.filtered_spans = []
        dlg.selected_span_idx = None
        dlg.photo_ref = None
        dlg.canvas_scale = 1.0
        dlg.canvas_offset = (0, 0)
        dlg.drag_start = None
        dlg.selected_rect = None

        # Top Bar: Page Navigation
        top_bar = tk.Frame(dlg, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(top_bar, text="Edit & Add Text", font=FONT_BOLD, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=16, pady=10)

        nav_frame = tk.Frame(top_bar, bg=SURFACE)
        nav_frame.pack(side=tk.RIGHT, padx=16, pady=6)

        btn_prev = HoverButton(nav_frame, "\u25c0", command=lambda: self._edit_change_page(dlg, -1), small=True, padx=8, pady=4)
        btn_prev.pack(side=tk.LEFT, padx=2)

        tk.Label(nav_frame, text="Page:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=(6, 2))
        page_var = tk.StringVar(value=str(dlg.current_page + 1))
        dlg.page_var = page_var
        page_entry = tk.Entry(nav_frame, textvariable=page_var, width=4, font=FONT_SMALL, justify=tk.CENTER,
                              relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        page_entry.pack(side=tk.LEFT, padx=2)
        total_lbl = tk.Label(nav_frame, text=f"/ {self.tool.page_count}", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED)
        total_lbl.pack(side=tk.LEFT, padx=(2, 6))

        btn_next = HoverButton(nav_frame, "\u25b6", command=lambda: self._edit_change_page(dlg, 1), small=True, padx=8, pady=4)
        btn_next.pack(side=tk.LEFT, padx=2)

        def on_page_submit(e=None):
            try:
                p = int(page_var.get()) - 1
                if 0 <= p < self.tool.page_count:
                    dlg.current_page = p
                    self._load_edit_page(dlg)
                else:
                    page_var.set(str(dlg.current_page + 1))
            except ValueError:
                page_var.set(str(dlg.current_page + 1))

        page_entry.bind("<Return>", on_page_submit)
        page_entry.bind("<FocusOut>", on_page_submit)

        # Tab Switcher
        tab_frame = tk.Frame(dlg, bg=BG)
        tab_frame.pack(fill=tk.X, padx=16, pady=(10, 6))

        content_container = tk.Frame(dlg, bg=BG)
        content_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        frame_digital = tk.Frame(content_container, bg=BG)
        frame_custom = tk.Frame(content_container, bg=BG)
        dlg.frame_digital = frame_digital
        dlg.frame_custom = frame_custom

        tab_mode = tk.StringVar(value="digital")
        dlg.tab_mode = tab_mode

        def switch_tab(mode):
            tab_mode.set(mode)
            if mode == "digital":
                frame_custom.pack_forget()
                frame_digital.pack(fill=tk.BOTH, expand=True)
                tab_btn_digital.config(bg=ACCENT, highlightbackground=ACCENT)
                tab_btn_digital.label.config(bg=ACCENT, fg="#ffffff")
                tab_btn_custom.config(bg=SURFACE, highlightbackground=BORDER)
                tab_btn_custom.label.config(bg=SURFACE, fg=TEXT)
            else:
                frame_digital.pack_forget()
                frame_custom.pack(fill=tk.BOTH, expand=True)
                tab_btn_custom.config(bg=ACCENT, highlightbackground=ACCENT)
                tab_btn_custom.label.config(bg=ACCENT, fg="#ffffff")
                tab_btn_digital.config(bg=SURFACE, highlightbackground=BORDER)
                tab_btn_digital.label.config(bg=SURFACE, fg=TEXT)
                self._render_edit_canvas(dlg)

        tab_btn_digital = HoverButton(tab_frame, "Replace Digital Text", command=lambda: switch_tab("digital"), primary=True, small=True)
        tab_btn_digital.pack(side=tk.LEFT, padx=(0, 6))
        dlg.tab_btn_digital = tab_btn_digital

        tab_btn_custom = HoverButton(tab_frame, "Add / Overwrite Free Text (For Scanned & Image PDFs)", command=lambda: switch_tab("custom"), small=True)
        tab_btn_custom.pack(side=tk.LEFT, padx=6)
        dlg.tab_btn_custom = tab_btn_custom
        dlg.switch_tab = switch_tab

        # ==========================================
        # TAB 1: REPLACE DIGITAL TEXT
        # ==========================================
        search_row = tk.Frame(frame_digital, bg=BG)
        search_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(search_row, text="Search text:", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))
        search_var = tk.StringVar()
        dlg.search_var = search_var
        search_entry = tk.Entry(search_row, textvariable=search_var, font=FONT_SMALL, relief=tk.FLAT,
                                highlightthickness=1, highlightbackground=BORDER)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search_var.trace_add("write", lambda *args: self._filter_digital_spans(dlg))

        digital_body = tk.Frame(frame_digital, bg=BG)
        digital_body.pack(fill=tk.BOTH, expand=True)
        dlg.digital_body = digital_body

        # 1. Empty Notice Frame (for scanned / image PDFs)
        empty_notice = tk.Frame(digital_body, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, padx=24, pady=28)
        tk.Label(empty_notice, text="Scanned / Image Document", font=FONT_BOLD, bg=SURFACE, fg=TEXT).pack(pady=(0, 6))
        tk.Label(empty_notice,
                 text="No digital text layer found on this page because the page is an image or scan.\n"
                      "To edit, replace, or add text to this scanned document, use the 'Add / Overwrite Free Text' mode.",
                 font=FONT, bg=SURFACE, fg=TEXT_MUTED, justify=tk.CENTER, wraplength=520).pack(pady=4)
        HoverButton(empty_notice, "Open Add / Overwrite Free Text Mode \u2794", command=lambda: switch_tab("custom"), primary=True).pack(pady=(14, 0))
        dlg.empty_notice = empty_notice

        # 2. Spans List View Frame (for digital PDFs)
        spans_view = tk.Frame(digital_body, bg=BG)
        dlg.spans_view = spans_view

        list_frame = tk.Frame(spans_view, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        listbox = tk.Listbox(list_frame, font=FONT_SMALL, bg=SURFACE, fg=TEXT,
                             selectbackground=ACCENT_SOFT, selectforeground=TEXT,
                             relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER,
                             activestyle="none")
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview, bg=BORDER,
                          activebackground="#c8ccd2", troughcolor=BG, relief=tk.FLAT)
        listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dlg.listbox = listbox

        # Detail & Edit Box
        edit_box = tk.Frame(spans_view, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=10)
        edit_box.pack(fill=tk.X, pady=(4, 0))

        info_label = tk.Label(edit_box, text="Select a text row above to edit.", font=FONT_SMALL,
                              bg=SURFACE, fg=TEXT_MUTED, anchor="w", justify=tk.LEFT)
        info_label.pack(fill=tk.X, pady=(0, 6))
        dlg.info_label = info_label

        tk.Label(edit_box, text="New text:", font=FONT_BOLD, bg=SURFACE, fg=TEXT, anchor="w").pack(fill=tk.X)
        entry_new_text = tk.Entry(edit_box, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_new_text.pack(fill=tk.X, pady=(2, 6))
        dlg.entry_new_text = entry_new_text

        opts_row = tk.Frame(edit_box, bg=SURFACE)
        opts_row.pack(fill=tk.X, pady=(0, 8))

        auto_shrink_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts_row, text="Auto-shrink font size if text is longer",
                       variable=auto_shrink_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT)

        whiteout_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts_row, text="Cover old text with white",
                       variable=whiteout_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT, padx=12)

        btn_row_digital = tk.Frame(edit_box, bg=SURFACE)
        btn_row_digital.pack(fill=tk.X)

        HoverButton(btn_row_digital, "Apply Changes",
                    command=lambda: self._do_digital_replace(dlg, entry_new_text, auto_shrink_var.get(), whiteout_var.get()),
                    primary=True, small=True).pack(side=tk.RIGHT, padx=4)

        HoverButton(btn_row_digital, "Replace All Matches",
                    command=lambda: self._do_digital_replace_all(dlg, entry_new_text, auto_shrink_var.get(), whiteout_var.get()),
                    small=True).pack(side=tk.RIGHT, padx=4)

        HoverButton(btn_row_digital, "Clear / Delete Text",
                    command=lambda: self._do_digital_clear(dlg),
                    danger=True, small=True).pack(side=tk.LEFT, padx=4)

        listbox.bind("<<ListboxSelect>>", lambda e: self._on_digital_span_select(dlg))

        # ==========================================
        # TAB 2: ADD / OVERWRITE FREE TEXT (SCAN & DIGITAL)
        # ==========================================
        split_frame = tk.Frame(frame_custom, bg=BG)
        split_frame.pack(fill=tk.BOTH, expand=True)

        # Left Column: Canvas Preview
        preview_col = tk.Frame(split_frame, bg=BG)
        preview_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        canvas_header = tk.Frame(preview_col, bg=BG)
        canvas_header.pack(fill=tk.X, pady=(0, 4))
        tk.Label(canvas_header, text="1. Click document to set text position:", font=FONT_BOLD, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        tk.Label(canvas_header, text="(or drag a box)", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=4)

        canvas_container = tk.Frame(preview_col, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        canvas_container.pack(fill=tk.BOTH, expand=True)

        canvas_page = tk.Canvas(canvas_container, bg="#e5e7eb", highlightthickness=0, cursor="crosshair")
        canvas_page.pack(fill=tk.BOTH, expand=True)
        dlg.canvas_page = canvas_page

        # Right Column: Controls Form
        form_col = tk.Frame(split_frame, bg=SURFACE, width=380, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=12)
        form_col.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(8, 0))
        form_col.pack_propagate(False)

        tk.Label(form_col, text="2. Enter text & set style:", font=FONT_BOLD, bg=SURFACE, fg=TEXT, anchor="w").pack(fill=tk.X, pady=(0, 6))

        tk.Label(form_col, text="Text to insert:", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X)
        text_custom_entry = tk.Text(form_col, height=4, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        text_custom_entry.pack(fill=tk.X, pady=(2, 8))

        pos_row = tk.Frame(form_col, bg=SURFACE)
        pos_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(pos_row, text="Position X:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        x_var = tk.StringVar(value="72")
        entry_x = tk.Entry(pos_row, textvariable=x_var, width=6, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_x.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(pos_row, text="Y:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        y_var = tk.StringVar(value="100")
        entry_y = tk.Entry(pos_row, textvariable=y_var, width=6, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        entry_y.pack(side=tk.LEFT, padx=(4, 6))
        tk.Label(pos_row, text="pt", font=FONT_SMALL, bg=SURFACE, fg=TEXT_MUTED).pack(side=tk.LEFT)

        f_row1 = tk.Frame(form_col, bg=SURFACE)
        f_row1.pack(fill=tk.X, pady=(0, 6))
        tk.Label(f_row1, text="Font:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        font_family_var = tk.StringVar(value="Helvetica (Sans)")
        om_font = tk.OptionMenu(f_row1, font_family_var, "Helvetica (Sans)", "Times (Serif)", "Courier (Mono)")
        om_font.config(font=FONT_SMALL, bg=SURFACE, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_font.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(f_row1, text="Size:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT)
        size_var = tk.StringVar(value="12")
        om_size = tk.Spinbox(f_row1, from_=6, to=72, textvariable=size_var, width=4, font=FONT_SMALL, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_size.pack(side=tk.LEFT, padx=(4, 0))

        f_row2 = tk.Frame(form_col, bg=SURFACE)
        f_row2.pack(fill=tk.X, pady=(0, 8))
        bold_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_row2, text="Bold", variable=bold_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT)
        italic_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_row2, text="Italic", variable=italic_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE).pack(side=tk.LEFT, padx=8)

        tk.Label(f_row2, text="Color:", font=FONT_SMALL, bg=SURFACE, fg=TEXT).pack(side=tk.LEFT, padx=(8, 4))
        color_var = tk.StringVar(value="Black")
        om_color = tk.OptionMenu(f_row2, color_var, "Black", "Blue", "Red", "Green", "White", "Gray")
        om_color.config(font=FONT_SMALL, bg=SURFACE, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
        om_color.pack(side=tk.LEFT)

        whiteout_custom_var = tk.BooleanVar(value=True)
        tk.Checkbutton(form_col, text="Cover background with white (Whiteout/overwrite scanned text)",
                       variable=whiteout_custom_var, bg=SURFACE, fg=TEXT, font=FONT_SMALL, activebackground=SURFACE,
                       wraplength=340, justify=tk.LEFT).pack(anchor="w", pady=(0, 10))

        custom_action_frame = tk.Frame(form_col, bg=SURFACE)
        custom_action_frame.pack(fill=tk.X, pady=(4, 0))

        HoverButton(custom_action_frame, "Insert Text into Document",
                    command=lambda: self._do_insert_custom_text(
                        dlg, text_custom_entry, x_var, y_var, size_var, font_family_var,
                        bold_var, italic_var, color_var, whiteout_custom_var
                    ),
                    primary=True).pack(fill=tk.X, pady=(0, 6))

        HoverButton(custom_action_frame, "Whiteout Area Only",
                    command=lambda: self._do_whiteout_only(dlg, x_var, y_var),
                    small=True).pack(fill=tk.X)

        status_custom_lbl = tk.Label(form_col, text="", font=FONT_SMALL, bg=SURFACE, fg=ACCENT, wraplength=340)
        status_custom_lbl.pack(fill=tk.X, pady=(8, 0))
        dlg.status_custom_lbl = status_custom_lbl

        # Canvas Mouse Interaction
        def on_canvas_click(e):
            dlg.drag_start = (e.x, e.y)
            canvas_page.delete("rubberband")

        def on_canvas_drag(e):
            if not dlg.drag_start:
                return
            canvas_page.delete("rubberband")
            canvas_page.create_rectangle(dlg.drag_start[0], dlg.drag_start[1], e.x, e.y,
                                         outline=ACCENT, dash=(2, 2), width=1.5, tags="rubberband")

        def on_canvas_release(e):
            if not dlg.drag_start:
                return
            x0, y0 = dlg.drag_start
            x1, y1 = e.x, e.y
            canvas_page.delete("rubberband")
            canvas_page.delete("marker")
            scale = getattr(dlg, "canvas_scale", 1.0)
            if scale <= 0:
                scale = 1.0
            ox, oy = getattr(dlg, "canvas_offset", (0, 0))

            if not self.tool.doc or dlg.current_page >= self.tool.page_count:
                return
            page = self.tool.doc.load_page(dlg.current_page)
            pw, ph = page.rect.width, page.rect.height

            if abs(x1 - x0) < 5 and abs(y1 - y0) < 5:
                # Single Click
                pdf_x = max(0.0, min(pw, (x0 - ox) / scale))
                pdf_y = max(0.0, min(ph, (y0 - oy) / scale))
                x_var.set(str(round(pdf_x, 1)))
                y_var.set(str(round(pdf_y, 1)))
                dlg.selected_rect = None
                canvas_page.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4, fill=DANGER, outline="#ffffff", width=1.5, tags="marker")
                status_custom_lbl.config(text=f"Selected position: ({pdf_x:.1f}, {pdf_y:.1f}) pt", fg=TEXT_MUTED)
            else:
                # Drag Box
                min_x = min(x0, x1)
                max_x = max(x0, x1)
                min_y = min(y0, y1)
                max_y = max(y0, y1)
                pdf_x = max(0.0, min(pw, (min_x - ox) / scale))
                pdf_y = max(0.0, min(ph, (min_y - oy) / scale))
                box_w = max(1.0, (max_x - min_x) / scale)
                box_h = max(1.0, (max_y - min_y) / scale)
                x_var.set(str(round(pdf_x, 1)))
                y_var.set(str(round(pdf_y, 1)))
                dlg.selected_rect = (pdf_x, pdf_y, box_w, box_h)
                canvas_page.create_rectangle(min_x, min_y, max_x, max_y, outline=DANGER, width=1.5, tags="marker")
                status_custom_lbl.config(text=f"Selected area: ({pdf_x:.1f}, {pdf_y:.1f}) size {box_w:.1f}x{box_h:.1f} pt", fg=TEXT_MUTED)
            dlg.drag_start = None

        canvas_page.bind("<Button-1>", on_canvas_click)
        canvas_page.bind("<B1-Motion>", on_canvas_drag)
        canvas_page.bind("<ButtonRelease-1>", on_canvas_release)
        canvas_page.bind("<Configure>", lambda e: self._render_edit_canvas(dlg) if dlg.tab_mode.get() == "custom" else None)

        # Initial Tab & Load
        switch_tab("digital")
        self._load_edit_page(dlg)

    def _edit_change_page(self, dlg, delta):
        if not self.tool.doc:
            return
        new_page = dlg.current_page + delta
        if 0 <= new_page < self.tool.page_count:
            dlg.current_page = new_page
            self._load_edit_page(dlg)

    def _load_edit_page(self, dlg):
        pno = dlg.current_page
        dlg.page_var.set(str(pno + 1))
        dlg.selected_span_idx = None
        dlg.selected_rect = None

        # Load digital text spans
        try:
            spans = self.tool.get_text_spans(pno)
        except Exception:
            spans = []
        dlg.spans = spans

        self._filter_digital_spans(dlg)

        if len(spans) == 0:
            dlg.empty_notice.pack(fill=tk.BOTH, expand=True)
            dlg.spans_view.pack_forget()
        else:
            dlg.empty_notice.pack_forget()
            dlg.spans_view.pack(fill=tk.BOTH, expand=True)

        if dlg.tab_mode.get() == "custom":
            self._render_edit_canvas(dlg)

    def _filter_digital_spans(self, dlg):
        query = dlg.search_var.get().lower().strip()
        dlg.listbox.delete(0, tk.END)
        dlg.filtered_spans = []
        for orig_idx, s in enumerate(dlg.spans):
            if not query or query in s["text"].lower():
                dlg.filtered_spans.append((orig_idx, s))
                label = f"{s['text'][:55]}  |  {s['font']}  |  {s['size']:.1f}pt"
                dlg.listbox.insert(tk.END, label)

        if len(dlg.spans) > 0:
            dlg.info_label.config(
                text=f"{len(dlg.filtered_spans)} of {len(dlg.spans)} text spans on page {dlg.current_page + 1}. Select one to edit.",
                fg=TEXT_MUTED,
            )
        dlg.entry_new_text.delete(0, tk.END)

    def _on_digital_span_select(self, dlg):
        sel = dlg.listbox.curselection()
        if not sel:
            return
        idx_in_filtered = sel[0]
        if idx_in_filtered >= len(dlg.filtered_spans):
            return
        orig_idx, s = dlg.filtered_spans[idx_in_filtered]
        dlg.selected_span_idx = orig_idx
        dlg.info_label.config(
            text=f"Font: {s['font']}  \u00b7  Size: {s['size']:.1f}pt  \u00b7  "
                 f"Color: #{s['color']:06x}  \u00b7  Position: ({s['bbox'].x0:.1f}, {s['bbox'].y0:.1f}) pt",
            fg=TEXT,
        )
        dlg.entry_new_text.delete(0, tk.END)
        dlg.entry_new_text.insert(0, s["text"])

    def _do_digital_replace(self, dlg, entry_new_text, auto_shrink, whiteout):
        if dlg.selected_span_idx is None:
            messagebox.showinfo("Edit Text", "Select the text you want to replace first.", parent=dlg)
            return
        new_text = entry_new_text.get()
        if not new_text:
            messagebox.showinfo("Edit Text", "New text cannot be empty.", parent=dlg)
            return
        try:
            result = self.tool.replace_text(
                dlg.current_page,
                dlg.selected_span_idx,
                new_text,
                auto_shrink=auto_shrink,
                allow_longer=True,
                whiteout=whiteout,
            )
        except ValueError as e:
            messagebox.showerror("Edit Text", str(e), parent=dlg)
            return

        msg = "Text replaced successfully."
        if result.get("warnings"):
            msg += "\n\nNotes:\n- " + "\n- ".join(result["warnings"])
        messagebox.showinfo("Edit Text", msg, parent=dlg)

        self._load_edit_page(dlg)
        self._refresh_preview()

    def _do_digital_replace_all(self, dlg, entry_new_text, auto_shrink, whiteout):
        if dlg.selected_span_idx is None or dlg.selected_span_idx >= len(dlg.spans):
            messagebox.showinfo("Edit Text", "Select the text you want to replace first.", parent=dlg)
            return
        orig_s = dlg.spans[dlg.selected_span_idx]
        old_text = orig_s["text"]
        new_text = entry_new_text.get()
        if not new_text:
            messagebox.showinfo("Edit Text", "New text cannot be empty.", parent=dlg)
            return
        if not messagebox.askyesno("Replace All", f"Replace all occurrences of '{old_text}' on this page with '{new_text}'?", parent=dlg):
            return
        try:
            result = self.tool.replace_all_text(
                dlg.current_page,
                old_text,
                new_text,
                auto_shrink=auto_shrink,
                whiteout=whiteout,
            )
        except ValueError as e:
            messagebox.showerror("Edit Text", str(e), parent=dlg)
            return

        msg = f"Successfully replaced {result['count']} text spans."
        if result.get("warnings"):
            msg += "\n\nNotes:\n- " + "\n- ".join(result["warnings"])
        messagebox.showinfo("Edit Text", msg, parent=dlg)

        self._load_edit_page(dlg)
        self._refresh_preview()

    def _do_digital_clear(self, dlg):
        if dlg.selected_span_idx is None or dlg.selected_span_idx >= len(dlg.spans):
            messagebox.showinfo("Edit Text", "Select the text you want to delete first.", parent=dlg)
            return
        if not messagebox.askyesno("Delete Text", "Delete/whiten the selected text?", parent=dlg):
            return
        s = dlg.spans[dlg.selected_span_idx]
        try:
            self.tool.whiteout_area(dlg.current_page, s["bbox"])
        except Exception as e:
            messagebox.showerror("Edit Text", f"Failed to delete text: {e}", parent=dlg)
            return
        messagebox.showinfo("Edit Text", "Text deleted successfully.", parent=dlg)
        self._load_edit_page(dlg)
        self._refresh_preview()

    def _render_edit_canvas(self, dlg):
        canvas = dlg.canvas_page
        canvas.delete("all")
        page_idx = dlg.current_page
        if not self.tool.doc or page_idx < 0 or page_idx >= self.tool.page_count:
            return
        page = self.tool.doc.load_page(page_idx)
        pw, ph = page.rect.width, page.rect.height

        canvas.update_idletasks()
        cw = max(canvas.winfo_width(), 360)
        ch = max(canvas.winfo_height(), 460)

        scale = min((cw - 24) / pw, (ch - 24) / ph)
        scale = max(0.1, min(2.0, scale))
        dlg.canvas_scale = scale

        target_w = int(pw * scale)
        target_h = int(ph * scale)

        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        photo = tk.PhotoImage(data=pix.tobytes("ppm"), width=pix.width, height=pix.height)
        dlg.photo_ref = photo

        ox = max(12, (cw - target_w) // 2)
        oy = max(12, (ch - target_h) // 2)
        dlg.canvas_offset = (ox, oy)

        canvas.create_image(ox, oy, image=photo, anchor="nw", tags="page_img")

    def _do_insert_custom_text(self, dlg, text_widget, x_var, y_var, size_var, font_var, bold_var, italic_var, color_var, whiteout_var):
        text = text_widget.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("Add Text", "Type the text you want to insert first.", parent=dlg)
            return
        try:
            x = float(x_var.get())
            y = float(y_var.get())
        except ValueError:
            messagebox.showerror("Add Text", "Position X and Y must be numbers.", parent=dlg)
            return
        try:
            fontsize = float(size_var.get())
        except ValueError:
            fontsize = 12.0

        color_map = {
            "Black": (0.0, 0.0, 0.0),
            "Blue": (0.1, 0.3, 0.8),
            "Red": (0.85, 0.15, 0.15),
            "Green": (0.1, 0.6, 0.2),
            "White": (1.0, 1.0, 1.0),
            "Gray": (0.45, 0.45, 0.45),
        }
        color = color_map.get(color_var.get(), (0.0, 0.0, 0.0))

        font_map = {
            "Helvetica (Sans)": "helv",
            "Times (Serif)": "times",
            "Courier (Mono)": "courier",
        }
        font_family = font_map.get(font_var.get(), "helv")

        box_w, box_h = None, None
        if hasattr(dlg, "selected_rect") and dlg.selected_rect:
            box_w, box_h = dlg.selected_rect[2], dlg.selected_rect[3]

        try:
            self.tool.add_custom_text(
                dlg.current_page,
                text,
                x=x,
                y=y,
                fontsize=fontsize,
                font_family=font_family,
                color=color,
                bold=bold_var.get(),
                italic=italic_var.get(),
                whiteout=whiteout_var.get(),
                box_width=box_w,
                box_height=box_h,
            )
        except Exception as e:
            messagebox.showerror("Add Text", f"Failed to add text: {e}", parent=dlg)
            return

        dlg.status_custom_lbl.config(text="\u2713 Text inserted successfully.", fg="#16a34a")
        dlg.selected_rect = None
        self._load_edit_page(dlg)
        self._render_edit_canvas(dlg)
        self._refresh_preview()

    def _do_whiteout_only(self, dlg, x_var, y_var):
        try:
            x = float(x_var.get())
            y = float(y_var.get())
        except ValueError:
            messagebox.showerror("Whiteout", "Position X and Y must be numbers.", parent=dlg)
            return

        if hasattr(dlg, "selected_rect") and dlg.selected_rect:
            w, h = dlg.selected_rect[2], dlg.selected_rect[3]
            rect = fitz.Rect(x, y, x + w, y + h)
        else:
            rect = fitz.Rect(x, y, x + 150, y + 25)

        try:
            self.tool.whiteout_area(dlg.current_page, rect)
        except Exception as e:
            messagebox.showerror("Whiteout", f"Failed to whiten area: {e}", parent=dlg)
            return

        dlg.status_custom_lbl.config(text=f"\u2713 Area ({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f}) whitened successfully.", fg="#16a34a")
        dlg.selected_rect = None
        self._load_edit_page(dlg)
        self._render_edit_canvas(dlg)
        self._refresh_preview()

    def stamp_image_ui(self):
        if not self._require_doc():
            return
        default_page = (self.tool.selection[0] + 1) if self.tool.selection else 1
        img_path = tk.StringVar()
        dlg = tk.Toplevel(self.root)
        dlg.title("Image Stamp / Signature")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        tk.Label(dlg, text="Image (PNG/JPG signature / stamp):", font=FONT, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 2))
        r1 = tk.Frame(dlg, bg=BG)
        r1.pack(fill=tk.X, padx=16)
        tk.Entry(r1, textvariable=img_path, font=FONT, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER).pack(side=tk.LEFT, fill=tk.X, expand=True)
        HoverButton(r1, "\u2026", command=lambda: img_path.set(native_open_file("Select stamp image", pdf_only=False)), small=True).pack(side=tk.LEFT, padx=(6, 0))
        r2 = tk.Frame(dlg, bg=BG)
        r2.pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(r2, text="Page:", font=FONT_SMALL, bg=BG, fg=TEXT).pack(side=tk.LEFT)
        page_var = tk.StringVar(value=str(default_page))
        tk.Entry(r2, textvariable=page_var, width=4, font=FONT_SMALL, relief=tk.FLAT,
                 highlightthickness=1, highlightbackground=BORDER, justify=tk.CENTER).pack(side=tk.LEFT, padx=(4, 14))
        tk.Label(r2, text=f"(max {self.tool.page_count})", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED).pack(side=tk.LEFT)
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(12, 12))
        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)

        def do_stamp():
            p = img_path.get().strip()
            if not p:
                messagebox.showinfo("Stamp", "Select an image file first.", parent=dlg)
                return
            try:
                pno = int(page_var.get()) - 1
                if not (0 <= pno < self.tool.page_count):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Stamp", "Invalid page number.", parent=dlg)
                return
            dlg.destroy()
            try:
                self.tool.stamp_image(pno, p)
            except ValueError as e:
                messagebox.showerror("Stamp", str(e), parent=self.root)
                return
            self._refresh_preview()

        HoverButton(btn_row, "Stamp at bottom-right", command=do_stamp, primary=True, small=True).pack(side=tk.LEFT, padx=6)

    # ---------- New feature: Metadata ----------
    def metadata_ui(self):
        if not self._require_doc():
            return
        meta = self.tool.get_metadata()
        size = meta.get("_file_size")
        dlg = tk.Toplevel(self.root)
        dlg.title("Document Info (Metadata)")
        dlg.transient(self.root)
        _safe_grab(dlg)
        dlg.configure(bg=BG)
        head = f"{os.path.basename(self.tool.path or '')}  \u00b7  {meta.get('_page_count', 0)} pages"
        if size:
            head += f"  \u00b7  {size / 1024:.1f} KB"
        if meta.get("_encrypted"):
            head += "  \u00b7  \U0001f512 encrypted"
        tk.Label(dlg, text=head, font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(fill=tk.X, padx=16, pady=(12, 6))
        fields = [("title", "Title"), ("author", "Author"), ("subject", "Subject"),
                  ("keywords", "Keywords"), ("creator", "Creator application")]
        entries = {}
        for i, (key, label) in enumerate(fields):
            tk.Label(dlg, text=f"{label}:", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16, pady=(6, 0))
            e = tk.Entry(dlg, font=FONT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER)
            e.insert(0, meta.get(key) or "")
            e.pack(fill=tk.X, padx=16)
            entries[key] = e
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(pady=(14, 12))

        def do_save():
            kwargs = {}
            for key, e in entries.items():
                cur = meta.get(key) or ""
                if e.get() != cur:
                    kwargs[key] = e.get()
            dlg.destroy()
            if not kwargs:
                return
            try:
                self.tool.set_metadata(**kwargs)
            except Exception as ex:
                messagebox.showerror("Metadata", f"Failed: {ex}", parent=self.root)
                return
            messagebox.showinfo("Metadata", "Metadata updated.\nUse Save to save to file.", parent=self.root)

        HoverButton(btn_row, "Cancel", command=dlg.destroy, small=True).pack(side=tk.LEFT, padx=6)
        HoverButton(btn_row, "Apply", command=do_save, primary=True, small=True).pack(side=tk.LEFT, padx=6)