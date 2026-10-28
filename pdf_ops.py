import fitz
import os
import math
import glob
import threading
import contextlib

# ---------- Fallback fonts (for text editing) ----------
_FONT_ROOTS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]


def _find_system_font(bold=False, italic=False, serif=False, mono=False):
    """Find a matching TTF/OTF system font by style. Returns a path or None."""
    if mono:
        names = ["DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"]
    elif serif:
        names = [
            "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf",
            "LiberationSerif-Bold.ttf" if bold else "LiberationSerif-Regular.ttf",
        ]
    else:
        names = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
        ]
    for root in _FONT_ROOTS:
        for name in names:
            for sub in ("truetype", "opentype", ""):
                p = os.path.join(root, sub, name) if sub else os.path.join(root, name)
                if os.path.isfile(p):
                    return p
    for root in _FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for p in glob.glob(os.path.join(root, "**", "*.ttf"), recursive=True):
            if os.path.basename(p).lower() in (n.lower() for n in names):
                return p
    return None


def _base14_code(bold=False, italic=False, serif=False, mono=False):
    """PyMuPDF Base-14 font code matching the style (last-resort fallback)."""
    key = (bool(bold), bool(italic))
    if mono:
        return {(False, False): "cour", (True, False): "cobo", (False, True): "coit", (True, True): "cobi"}[key]
    if serif:
        return {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"}[key]
    return {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}[key]


class PDFTool:
    """Document state + all mutating operations.

    Every mutation is undoable: callers invoke :meth:`checkpoint` *before*
    changing the document; the pre-state snapshot is kept in an in-memory
    history that is capped by step count and total byte size, so opening a
    huge PDF cannot exhaust RAM.
    """

    HISTORY_MAX_STEPS = 30
    HISTORY_MEMORY_BUDGET = 192 * 1024 * 1024

    def __init__(self):
        self.doc = None
        self.path = None
        self.source_name = None   # naming hint for exports of detached copies
        self._selection = set()
        self.was_encrypted = False  # was the document encrypted when opened?
        self._history = []   # [(label, bytes, selection)]
        self._redo = []      # [(label, bytes, selection)]
        self._dirty = False
        self._lock = threading.RLock()   # guards doc access from worker threads
        self._in_batch = 0               # >0 while a batch()/checkpointed block runs

    def open(self, path, password=None):
        try:
            doc = fitz.open(path)
        except Exception as e:
            raise ValueError(f"Failed to open PDF: {e}")
        encrypted = bool(doc.needs_pass)
        if doc.needs_pass:
            if not password:
                doc.close()
                raise ValueError("NEEDS_PASSWORD")
            if not doc.authenticate(password):
                doc.close()
                raise ValueError("Incorrect password.")
        self.close()
        self.doc = doc
        self.path = path
        self.source_name = os.path.splitext(os.path.basename(path))[0]
        self._selection = set()
        self.was_encrypted = encrypted
        self._history.clear()
        self._redo.clear()
        self._dirty = False


    @classmethod
    def from_bytes(cls, data, password=None, source_name=None):
        """Build a detached tool from PDF bytes (for worker threads).

        The returned instance is independent of the UI's document, so a job can
        mutate it without racing the renderer; hand :meth:`to_bytes` back to the
        main thread and apply it with ``PDFTool.replace_document``.
        """
        tool = cls()
        tool.open_stream(data, password)
        tool.source_name = source_name
        return tool

    def open_stream(self, data, password=None):
        """Load a document from memory (no path, no recent-file entry)."""
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Failed to read PDF data: {e}")
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                doc.close()
                raise ValueError("Incorrect password.")
            self.was_encrypted = True
        self.close()
        self.doc = doc
        self.path = None
        self._selection = set()
        self._dirty = False
        return self

    def to_bytes(self):
        """Serialise for hand-off to the UI thread (garbage collected, compressed)."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        return self.doc.tobytes(garbage=3, deflate=True)

    def close(self):
        if self.doc:
            self.doc.close()
            self.doc = None
            self.path = None
            self._selection = set()
        self._history.clear()
        self._redo.clear()
        self._dirty = False

    @property
    def page_count(self):
        return self.doc.page_count if self.doc else 0

    @property
    def selection(self):
        return sorted(self._selection)

    def toggle_select(self, index):
        if index in self._selection:
            self._selection.remove(index)
        else:
            self._selection.add(index)
        return self.selection

    def clear_selection(self):
        self._selection.clear()

    def select_all(self):
        self._selection = set(range(self.page_count))

    def get_thumb(self, index, max_width=140):
        page = self.doc.load_page(index)
        w = page.rect.width or 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(max_width / w, max_width / w))
        return pix

    def delete_pages(self, indexes):
        self._auto_checkpoint("Delete pages")

        if not indexes:
            raise ValueError("No pages selected.")
        indexes = sorted(set(indexes))
        for i in reversed(indexes):
            self.doc.delete_page(i)
        self._selection.clear()

    def insert_from(self, other_path, src_indexes, position):
        self._auto_checkpoint("Insert pages")

        other = fitz.open(other_path)
        try:
            if position < 0 or position > self.page_count:
                raise ValueError("Invalid insert position.")
            src_indexes = sorted(set(src_indexes))
            for i in src_indexes:
                if i < 0 or i >= other.page_count:
                    raise ValueError(f"Page {i+1} does not exist in the source PDF.")
            other.select(src_indexes)
            self.doc.insert_pdf(other, start_at=position)
        finally:
            other.close()
        self._selection.clear()

    def merge(self, paths):
        self._auto_checkpoint("Merge PDFs")

        if not self.doc:
            raise ValueError("No PDF is open. Open or provide a parent document first.")
        for p in paths:
            src = fitz.open(p)
            try:
                self.doc.insert_pdf(src)
            finally:
                src.close()
        self._selection.clear()

    def split(self, start, end):
        if start < 0 or end >= self.page_count or start > end:
            raise ValueError("Invalid page range.")
        out = fitz.open()
        out.insert_pdf(self.doc, from_page=start, to_page=end)
        return out

    def split_all(self):
        out = fitz.open()
        out.insert_pdf(self.doc)
        return out

    def rotate(self, indexes, angle):
        self._auto_checkpoint("Rotate pages")

        if not indexes:
            raise ValueError("No pages selected.")
        for i in indexes:
            page = self.doc.load_page(i)
            page.set_rotation((page.rotation + angle) % 360)

    def move_pages(self, indexes, target_pos):
        self._auto_checkpoint("Reorder pages")

        if not indexes:
            raise ValueError("No pages selected.")
        indexes = sorted(set(indexes))
        count = len(indexes)
        total = self.page_count
        if target_pos < 0 or target_pos > total - count:
            raise ValueError("Invalid target position.")
        remaining = [i for i in range(total) if i not in indexes]
        # insert the selected block at target_pos within the remaining list
        new_order = remaining[:target_pos] + indexes + remaining[target_pos:]
        self.doc.select(new_order)
        self._selection.clear()

    def add_watermark(self, text=None, image_path=None, opacity=0.3, angle=45):
        self._auto_checkpoint("Watermark")

        if not text and not image_path:
            raise ValueError("Provide watermark text or choose an image.")
        for pno in range(self.page_count):
            page = self.doc.load_page(pno)
            rect = page.rect
            if text:
                fs = max(24, rect.width / 12)
                cx, cy = rect.width / 2, rect.height / 2
                a = math.radians(angle)
                rot = fitz.Matrix(math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
                page.insert_text(
                    fitz.Point(cx, cy),
                    text,
                    fontsize=fs,
                    color=(0.5, 0.5, 0.5),
                    fill_opacity=opacity,
                    stroke_opacity=opacity,
                    morph=(fitz.Point(cx, cy), rot),
                    overlay=True,
                )
            elif image_path:
                if not os.path.exists(image_path):
                    raise ValueError("Image file not found.")
                img = fitz.Pixmap(image_path)
                w = rect.width * 0.4
                h = w * img.height / img.width
                page.insert_image(
                    fitz.Rect((rect.width - w) / 2, (rect.height - h) / 2, (rect.width + w) / 2, (rect.height + h) / 2),
                    filename=image_path,
                    overlay=True,
                )
                img = None

    def _save_over_original(self, target, **save_kwargs):
        """Save the document safely, including overwriting the currently open file.

        PyMuPDF forbids doc.save() to the original path without incremental=True
        ("save to original must be incremental"). Trick: save to a temp file in
        the same folder, then atomically replace.
        """
        if not self.doc:
            raise ValueError("No PDF is open.")
        if target and os.path.abspath(target) == os.path.abspath(self.path or ""):
            tmp = target + ".tmp-save.pdf"
            self.doc.save(tmp, **save_kwargs)
            os.replace(tmp, target)
        else:
            self.doc.save(target or "_output.pdf", **save_kwargs)

    def protect(self, password, owner_password=None):
        if not password:
            raise ValueError("Password cannot be empty.")
        owner = owner_password or password
        self._save_over_original(
            self.path or "_protected.pdf",
            encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner, user_pw=password,
        )

    def unlock(self, out_path=None):
        """Save an unprotected copy (document must already be authenticated)."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        if not (self.was_encrypted or self.doc.is_encrypted or self.doc.needs_pass):
            raise ValueError("This PDF is not encrypted - nothing to unlock.")
        target = out_path or (os.path.splitext(self.path)[0] + "_unlocked.pdf")
        # permissions=-1 & encryption NONE -> restriction-free copy
        self._save_over_original(target, encryption=fitz.PDF_ENCRYPT_NONE, permissions=int(-1))
        return target

    def save_as(self, path):
        self.doc.save(path, garbage=3, deflate=True)
        self._dirty = False

    def save_to_bytes(self):
        return self.doc.tobytes(garbage=3, deflate=True)

    # ---------- Undo / redo / dirty state ----------

    @property
    def dirty(self):
        return self._dirty

    @property
    def can_undo(self):
        return bool(self._history) and self.doc is not None

    @property
    def can_redo(self):
        return bool(self._redo) and self.doc is not None

    @property
    def undo_label(self):
        return self._history[-1][0] if self._history else None

    @property
    def redo_label(self):
        return self._redo[-1][0] if self._redo else None

    def mark_clean(self):
        """Record that the in-memory document matches what is on disk."""
        self._dirty = False

    def snapshot_bytes(self):
        """Serialise the current document (fast: no garbage collection)."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        return self.doc.tobytes(deflate=True)

    def checkpoint(self, label="Edit"):
        """Capture the pre-mutation state.  Call this *before* changing anything.

        Cheap enough for interactive use on normal documents (a few tens of ms);
        history is trimmed oldest-first by :attr:`HISTORY_MEMORY_BUDGET`.
        A no-op without an open document so callers do not need to guard.
        """
        if not self.doc:
            return
        try:
            data = self.snapshot_bytes()
        except Exception:
            return
        self._history.append((label, data, self.selection))
        self._redo.clear()
        self._dirty = True
        self._trim_history()

    def _auto_checkpoint(self, label):
        """Snapshot the pre-mutation state unless a batch already did.

        Every public mutator calls this, so actions become undoable without each
        mixin having to remember; a surrounding :meth:`batch` never pays for a
        second (redundant) snapshot.
        """
        if self._in_batch or not self.doc:
            return
        self.checkpoint(label)

    def _trim_history(self):
        while len(self._history) > self.HISTORY_MAX_STEPS:
            self._history.pop(0)
        total = sum(len(d) for _, d, _ in self._history)
        while len(self._history) > 1 and total > self.HISTORY_MEMORY_BUDGET:
            _, data, _ = self._history.pop(0)
            total -= len(data)

    def _restore(self, data, selection):
        doc = fitz.open(stream=data, filetype="pdf")
        if self.doc:
            self.doc.close()
        self.doc = doc
        self._selection = set(selection)

    def undo(self):
        """Revert one step.  Returns the label of the reverted action, or None."""
        if not self.can_undo:
            return None
        label, data, sel = self._history.pop()
        self._redo.append((label, self.snapshot_bytes(), self.selection))
        self._restore(data, sel)
        self._dirty = True
        return label

    def redo(self):
        """Re-apply the last undone step.  Returns its label, or None."""
        if not self.can_redo:
            return None
        label, data, sel = self._redo.pop()
        self._history.append((label, self.snapshot_bytes(), self.selection))
        self._restore(data, sel)
        self._dirty = True
        return label

    # ---------- Read-only helpers safe for worker threads ----------

    @property
    def has_document(self):
        return self.doc is not None

    def first_page_size(self):
        """Size of page 1 in points (used to lay out the preview skeleton)."""
        if not self.doc or self.doc.page_count == 0:
            return (612.0, 792.0)
        rect = self.doc.load_page(0).rect
        return (rect.width, rect.height)

    def page_size(self, index):
        rect = self.doc.load_page(index).rect
        return (rect.width, rect.height)

    def search_text(self, needle, pages=None, limit=5000):
        """Find ``needle`` and return ``[{page, rect, text, line}]`` hits."""
        if not self.doc or not needle:
            return []
        if pages is None:
            pages = range(self.doc.page_count)
        hits = []
        for pno in pages:
            if pno < 0 or pno >= self.doc.page_count:
                continue
            page = self.doc.load_page(pno)
            for rect in page.search_for(needle):
                hits.append({"page": pno, "rect": fitz.Rect(rect), "text": needle,
                             "line": _line_text_near(page, rect)})
                if len(hits) >= limit:
                    return hits
        return hits

    # ---------- Transactional helpers ----------

    def replace_document(self, data, label="Edit"):
        """Swap in a document produced elsewhere (worker result, restore, ...)."""
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            doc.close()
            raise ValueError("The replacement document is encrypted.")
        old = self.doc
        self.doc = doc
        self._selection.clear()
        self._dirty = True
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        return label

    @contextlib.contextmanager
    def batch(self, label="Batch operation"):
        """Run several mutations as one undo step; roll back completely on error.

        Without this a failed multi-step action (e.g. redacting 40 matches) can
        leave the document half-edited with no way back.
        """
        if not self.doc:
            yield self
            return
        self.checkpoint(label)
        try:
            data = self.snapshot_bytes()
        except Exception:
            data = None
        sel = self.selection
        self._in_batch += 1
        try:
            yield self
        except BaseException:
            if data is not None:
                self._restore(data, sel)
                self._dirty = True
            raise
        finally:
            self._in_batch = max(0, self._in_batch - 1)

    # ---------- New feature: Text Editing ----------

    def get_text_spans(self, page_index):
        """Collect the list of text spans on a page (for the text-editing feature).

        Each span: text, bbox, origin (baseline), font, size, color (sRGB int),
        alpha, flags, dir (line direction).
        """
        page = self.doc.load_page(page_index)
        spans = []
        for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        continue
                    spans.append({
                        "text": text,
                        "bbox": fitz.Rect(span["bbox"]),
                        "origin": fitz.Point(span["origin"]),
                        "font": span["font"],
                        "size": span["size"],
                        "color": span.get("color", 0),
                        "alpha": span.get("alpha", 255),
                        "flags": span.get("flags", 0),
                        "dir": line.get("dir", (1.0, 0.0)),
                    })
        return spans

    def _page_font_ref(self, page, basefont):
        """Find the page font reference name ('/F1') matching basefont."""
        for f in page.get_fonts(full=True):
            if f[3] == basefont:
                name = f[4]
                return name if name.startswith("/") else "/" + name
        return None

    def _font_from_page(self, page, refname):
        """Build a fitz.Font from a font already on the page (for width measuring)."""
        xref = None
        for f in page.get_fonts(full=True):
            name = f[4]
            name = name if name.startswith("/") else "/" + name
            if name == refname:
                xref = f[0]
                break
        if xref is None:
            return fitz.Font(fontname="helv")
        try:
            _, _, _, buffer = self.doc.extract_font(xref)
            if buffer:
                return fitz.Font(fontbuffer=buffer)
        except Exception:
            pass
        return fitz.Font(fontname="helv")

    def _resolve_font(self, page, span, bold, italic, serif, mono, warnings):
        """Pick the insertion font: page original > system fontfile > Base-14."""
        basefont = span["font"]
        refname = self._page_font_ref(page, basefont)
        if refname:
            return refname, None, basefont
        path = _find_system_font(bold=bold, italic=italic, serif=serif, mono=mono)
        if path:
            warnings.append(
                f"Original font '{basefont}' unavailable for insertion; falling back to {os.path.basename(path)}"
            )
            return "f0", path, os.path.basename(path)
        code = _base14_code(bold, italic, serif, mono)
        warnings.append(f"Original font '{basefont}' unavailable; Base-14 fallback: {code}")
        return code, None, code

    def replace_text(
        self,
        page_index,
        span_index,
        new_text,
        auto_shrink=False,
        allow_longer=False,
        whiteout=True,
        custom_fontsize=None,
        custom_color=None,
    ):
        """Replace a single text span while preserving its original style.

        Precision: font/size/color/baseline are copied from the original span.
        Supports auto_shrink to automatically fit longer text, and whiteout to
        thoroughly clean the background behind it.

        Returns dict: {ok, font_used, warnings, replaced}
        """
        self._auto_checkpoint("Edit text")
        page = self.doc.load_page(page_index)
        spans = self.get_text_spans(page_index)
        if span_index < 0 or span_index >= len(spans):
            raise ValueError("Invalid text selection.")
        span = spans[span_index]
        old_text = span["text"]
        if not new_text:
            raise ValueError("New text cannot be empty.")
        if new_text == old_text:
            return {"ok": True, "font_used": span["font"], "warnings": [], "replaced": False}

        bbox = span["bbox"]
        origin = span["origin"]
        size = float(custom_fontsize) if custom_fontsize is not None else float(span["size"])
        color = custom_color if custom_color is not None else tuple(c / 255.0 for c in fitz.sRGB_to_rgb(span["color"]))
        alpha = span.get("alpha", 255)
        flags = span.get("flags", 0)
        bold = bool(flags & fitz.TEXT_FONT_BOLD)
        italic = bool(flags & fitz.TEXT_FONT_ITALIC)
        serif = bool(flags & fitz.TEXT_FONT_SERIFED)
        mono = bool(flags & fitz.TEXT_FONT_MONOSPACED)
        line_dir = span["dir"]

        warnings = []
        fontname, fontfile, font_used = self._resolve_font(page, span, bold, italic, serif, mono, warnings)

        # Measure new-text width using the chosen font
        try:
            if fontfile:
                probe = fitz.Font(fontfile=fontfile)
            elif fontname.startswith("/"):
                probe = self._font_from_page(page, fontname)
            else:
                probe = fitz.Font(fontname=fontname)
            new_len = probe.text_length(new_text, fontsize=size)
        except Exception:
            new_len = len(new_text) * size * 0.55

        if new_len > bbox.width + 1.0:
            if auto_shrink and new_len > 0:
                scale_factor = (bbox.width) / new_len
                size = max(5.0, size * scale_factor)
                warnings.append(
                    f"Font size adjusted ({span['size']:.1f}pt \u2192 {size:.1f}pt) to fit the area."
                )
            elif not allow_longer:
                raise ValueError(
                    f"New text is too long: {new_len:.1f}pt vs. original area {bbox.width:.1f}pt.\n"
                    "It would overlap other text. Shorten the text, enable auto-fit, or edit word by word."
                )

        # Remove the original text from the document stream
        page.add_redact_annot(bbox)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )

        if whiteout:
            page.draw_rect(bbox, fill=(1, 1, 1), color=None, overlay=True)

        # Insert the new text with the original properties
        kwargs = {
            "fontsize": size,
            "color": color,
            "fill_opacity": alpha / 255.0,
            "stroke_opacity": alpha / 255.0,
            "overlay": True,
        }
        if fontfile:
            kwargs["fontname"] = "f0"
            kwargs["fontfile"] = fontfile
        else:
            kwargs["fontname"] = fontname
        dx, dy = line_dir
        if abs(dx - 1.0) > 1e-3 or abs(dy) > 1e-3:
            kwargs["morph"] = (origin, fitz.Matrix(dx, dy, -dy, dx, 0, 0))
        page.insert_text(origin, new_text, **kwargs)

        return {"ok": True, "font_used": font_used, "warnings": warnings, "replaced": True}

    def replace_all_text(self, page_index, old_text, new_text, auto_shrink=True, whiteout=True):
        self._auto_checkpoint("Replace text")

        """Replace every span on the page equal to or containing old_text.

        IMPORTANT: each replace_text call runs apply_redactions, which reshapes
        the page's span structure. Processing indices top-down shifts spans after
        each edit -> wrong targets / lost text (data-corruption bug). Solution:
        collect all matching indices from the initial snapshot, then process from
        the LARGEST index down; removals only shift later spans, so smaller
        indices stay valid.
        """
        spans = self.get_text_spans(page_index)
        matched = [idx for idx, s in enumerate(spans) if old_text in s["text"]]
        count = 0
        all_warnings = []
        for idx in reversed(matched):
            updated = spans[idx]["text"].replace(old_text, new_text)
            res = self.replace_text(
                page_index, idx, updated, auto_shrink=auto_shrink, allow_longer=True, whiteout=whiteout
            )
            if res.get("replaced", True):
                count += 1
            all_warnings.extend(res.get("warnings", []))
        return {"ok": True, "count": count, "warnings": all_warnings}

    def add_custom_text(
        self,
        page_index,
        text,
        x,
        y,
        fontsize=12,
        font_family="helv",
        color=(0, 0, 0),
        bold=False,
        italic=False,
        whiteout=True,
        whiteout_padding=3,
        box_width=None,
        box_height=None,
    ):
        """Insert free-form text at page coordinates (x, y).

        Supports multi-line text and a whiteout option (white background) to
        overwrite text on scanned/image documents as well as digital ones.
        """
        self._auto_checkpoint("Insert text")
        page = self.doc.load_page(page_index)
        if not text:
            raise ValueError("Text cannot be empty.")

        font_code = _base14_code(
            bold=bold,
            italic=italic,
            serif=(font_family == "times"),
            mono=(font_family == "courier"),
        )

        lines = text.split("\n")
        line_height = fontsize * 1.25

        try:
            probe = fitz.Font(fontname=font_code)
            max_line_width = max(probe.text_length(line, fontsize=fontsize) for line in lines)
        except Exception:
            max_line_width = max(len(line) * fontsize * 0.6 for line in lines)

        total_height = len(lines) * line_height

        w = box_width if (box_width and box_width > 0) else (max_line_width + (whiteout_padding * 2))
        h = box_height if (box_height and box_height > 0) else (total_height + (whiteout_padding * 2))

        rect = fitz.Rect(
            x - whiteout_padding,
            y - whiteout_padding,
            x - whiteout_padding + w,
            y - whiteout_padding + h,
        )

        if whiteout:
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
            page.draw_rect(rect, fill=(1, 1, 1), color=None, overlay=True)

        for i, line in enumerate(lines):
            baseline_y = y + (i * line_height) + (fontsize * 0.85)
            origin = fitz.Point(x, baseline_y)
            page.insert_text(
                origin,
                line,
                fontsize=fontsize,
                fontname=font_code,
                color=color,
                overlay=True,
            )

        return {"ok": True, "rect": rect, "lines_count": len(lines)}

    def whiteout_area(self, page_index, rect):
        self._auto_checkpoint("Whiteout area")

        """Erase / cover a given area with solid white."""
        page = self.doc.load_page(page_index)
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )
        page.draw_rect(rect, fill=(1, 1, 1), color=None, overlay=True)
        return {"ok": True}

    # ---------- New features: Compress & Page Numbers ----------

    # ---------- Compression ----------

    # Presets: JPEG quality + DPI downsampling ceiling for embedded images.
    # "balanced" keeps scans/photos crisp at a fraction of the original size;
    # "extreme" is the smallest output that stays free of visible artifacts.
    COMPRESS_PRESETS = {
        "light": {"quality": 82, "dpi_target": 200},
        "balanced": {"quality": 68, "dpi_target": 150},
        "strong": {"quality": 52, "dpi_target": 120},
        "extreme": {"quality": 42, "dpi_target": 100},
    }

    # Hard floors. Below these values images start to look visibly degraded
    # (blocking/pixelation), so the search never goes past them - if the
    # requested target cannot be reached above the floors we say so honestly.
    _MIN_QUALITY = 40
    _MIN_DPI = 96

    @classmethod
    def _compress_params(cls, quality, dpi_target):
        """Build rewrite_images kwargs. dpi_threshold sits slightly above
        dpi_target so images already at/below the target DPI are untouched."""
        dpi_target = int(round(dpi_target))
        quality = int(round(quality))
        return {
            "quality": quality,
            "dpi_target": dpi_target,
            "dpi_threshold": int(round(dpi_target * 1.15)) + 1,
            "lossy": True,   # re-encode existing JPEGs at lower quality
            "lossless": True,  # convert PNG/Flate images to JPEG too
        }

    def _compress_snapshot(self, base_bytes, params):
        """Compress a fresh in-memory copy of the document and return bytes.

        Working on a snapshot keeps the live document (and its preview)
        untouched, which makes iterative target-size search possible.
        """
        doc = fitz.open(stream=base_bytes, filetype="pdf")
        try:
            if params:
                doc.rewrite_images(**params)
            try:
                doc.subset_fonts()
            except Exception:
                pass
            return doc.tobytes(garbage=4, deflate=True)
        finally:
            doc.close()

    @classmethod
    def _interpolate_params(cls, preset_key, t):
        """Parameter set at aggressiveness t (0 = preset ceiling, 1 = floor)."""
        p = cls.COMPRESS_PRESETS.get(preset_key, cls.COMPRESS_PRESETS["balanced"])
        quality = p["quality"] + t * (cls._MIN_QUALITY - p["quality"])
        dpi = p["dpi_target"] + t * (cls._MIN_DPI - p["dpi_target"])
        return cls._compress_params(quality, dpi)

    def compress_to_size(self, path=None, target_mb=1.0, preset="balanced", progress=None):
        """Compress until the output fits within ``target_mb``, maximising quality.

        Pass 1 is purely lossless (garbage collection, stream deflate, font
        subsetting) - if that alone meets the target, image quality is 100%.
        Otherwise a binary search runs between the preset ceiling and the
        quality floor, converging in a handful of passes.

        Returns a dict: size_before, size_after, target_reached, params_used.
        """
        if not self.doc:
            raise ValueError("No PDF is open.")
        if target_mb <= 0:
            raise ValueError("Target size must be greater than 0 MB.")

        target = int(target_mb * 1024 * 1024)
        size_before = (
            os.path.getsize(self.path)
            if self.path and os.path.isfile(self.path)
            else len(self.doc.tobytes())
        )
        base_bytes = self.doc.tobytes()

        if progress:
            progress("Optimizing without quality loss...")
        data = self._compress_snapshot(base_bytes, None)
        best_data, best_size, best_params = data, len(data), None

        if best_size > target:
            lo, hi = 0.0, 1.0
            last_size = -1
            for _ in range(7):
                mid = (lo + hi) / 2
                if progress:
                    progress(f"Trying quality level {mid:.0%}...")
                params = self._interpolate_params(preset, mid)
                data = self._compress_snapshot(base_bytes, params)
                size = len(data)
                if size <= target:
                    best_data, best_size, best_params = data, size, params
                    lo = mid
                else:
                    hi = mid
                if size == last_size or hi - lo < 0.06:
                    break
                last_size = size
            if best_size > target:
                # Nothing above the floor fits - take the floor itself.
                if progress:
                    progress("Using maximum compression allowed...")
                params = self._interpolate_params(preset, 1.0)
                data = self._compress_snapshot(base_bytes, params)
                if len(data) < best_size:
                    best_data, best_size, best_params = data, len(data), params

        out_path = path or self.path or "_compressed.pdf"
        tmp = out_path + ".tmp-compress"
        with open(tmp, "wb") as f:
            f.write(best_data)
        os.replace(tmp, out_path)

        return {
            "size_before": size_before,
            "size_after": best_size,
            "target_reached": best_size <= target,
            "params_used": best_params,
        }

    def compress(self, path=None, images=True, preset="balanced"):
        """Single-pass compression: garbage collection, deflate, font
        subsetting and one image rewrite at the given preset."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        p = self.COMPRESS_PRESETS.get(preset, self.COMPRESS_PRESETS["balanced"])
        base_bytes = self.doc.tobytes()
        params = self._compress_params(p["quality"], p["dpi_target"]) if images else None
        data = self._compress_snapshot(base_bytes, params)
        target = path or self.path
        tmp = target + ".tmp-compress" if target else "_output.tmp-compress.pdf"
        with open(tmp, "wb") as f:
            f.write(data)
        if target:
            os.replace(tmp, target)
        else:
            os.replace(tmp, "_output.pdf")

    def add_page_numbers(self, start=1, position="bottom", fontsize=11, color=(0.45, 0.45, 0.45)):
        self._auto_checkpoint("Page numbers")

        """Add page numbers to every page."""
        for i in range(self.page_count):
            page = self.doc.load_page(i)
            rect = page.rect
            text = str(start + i)
            w = fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
            if position == "top":
                p = fitz.Point((rect.width - w) / 2, 24)
            elif position == "bottom-right":
                p = fitz.Point(rect.width - 36 - w, rect.height - 24)
            elif position == "bottom-left":
                p = fitz.Point(36, rect.height - 24)
            else:  # bottom center
                p = fitz.Point((rect.width - w) / 2, rect.height - 24)
            page.insert_text(p, text, fontsize=fontsize, color=color, overlay=True)

    # ---------- New feature: Export to Images ----------

    def export_images(self, page_indices, out_dir, fmt="png", dpi=150, quality=90, progress_cb=None):
        """Export pages as PNG/JPEG image files.

        Returns the list of written file paths.
        """
        if not self.doc:
            raise ValueError("No PDF is open.")
        if not page_indices:
            raise ValueError("No pages selected.")
        fmt = (fmt or "png").lower()
        if fmt not in ("png", "jpeg", "jpg"):
            raise ValueError(f"Unsupported format: {fmt} (use png/jpeg)")
        ext = "jpg" if fmt in ("jpeg", "jpg") else "png"
        for idx in page_indices:
            if not (0 <= idx < self.page_count):
                raise ValueError(f"Invalid page number: {idx + 1}")
        base = self.source_name or "page"
        written = []
        total = len(page_indices)
        for i, idx in enumerate(page_indices):
            page = self.doc.load_page(idx)
            pix = page.get_pixmap(dpi=dpi)
            out_path = os.path.join(out_dir, f"{base}_page-{idx + 1}.{ext}")
            if ext == "jpg":
                pix.save(out_path, jpg_quality=quality)
            else:
                pix.save(out_path)
            pix = None
            written.append(out_path)
            if progress_cb:
                progress_cb(i + 1, total)
        return written

    # ---------- New features: Organization & More ----------

    def reverse_pages(self):
        self._auto_checkpoint("Reverse page order")

        """Reverse the order of all pages (last becomes first)."""
        if not self.doc or self.page_count < 2:
            return
        self.doc.select(list(range(self.page_count - 1, -1, -1)))
        self._selection.clear()

    def crop_pages(self, indexes=None, left=0.05, top=0.05, right=0.05, bottom=0.05):
        self._auto_checkpoint("Crop pages")

        """Trim page edges (ratio values 0-0.45 of page width/height)."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        for name, val in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
            if not (0 <= float(val) <= 0.45):
                raise ValueError(f"{name} crop ratio must be 0 - 0.45.")
        targets = sorted(set(indexes)) if indexes else list(range(self.page_count))
        if not targets:
            raise ValueError("No pages selected.")
        for i in targets:
            if not (0 <= i < self.page_count):
                raise ValueError(f"Invalid page number: {i + 1}")
            page = self.doc.load_page(i)
            r = page.rect
            dx, dy = r.width * float(left), r.height * float(top)
            dx2, dy2 = r.width * float(right), r.height * float(bottom)
            box = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 - dx2, r.y1 - dy2)
            if box.is_empty or box.width < 20 or box.height < 20:
                raise ValueError(f"Cropped page {i + 1} would be too small.")
            page.set_cropbox(box)

    def stamp_image(self, page_index, image_path, x=None, y=None, width_ratio=0.3):
        self._auto_checkpoint("Stamp image")

        """Stamp an image (signature / stamp) onto a page.

        x, y = top-left corner position in pt. Defaults to bottom-right.
        """
        if not self.doc:
            raise ValueError("No PDF is open.")
        if not image_path or not os.path.exists(image_path):
            raise ValueError("Image file not found.")
        page = self.doc.load_page(page_index)
        rect = page.rect
        try:
            img = fitz.Pixmap(image_path)
            ratio = img.height / img.width if img.width else 1.0
            img = None
        except Exception as e:
            raise ValueError(f"Failed to read image: {e}")
        w = rect.width * float(width_ratio)
        h = w * ratio
        if x is None:
            x = rect.width - w - 40
        if y is None:
            y = rect.height - h - 40
        x = max(0.0, min(rect.width - 10, float(x)))
        y = max(0.0, min(rect.height - 10, float(y)))
        target = fitz.Rect(x, y, min(x + w, rect.width), min(y + h, rect.height))
        page.insert_image(target, filename=image_path, overlay=True)
        return {"ok": True, "rect": target}

    def get_metadata(self):
        if not self.doc:
            raise ValueError("No PDF is open.")
        meta = dict(self.doc.metadata or {})
        meta["_page_count"] = self.page_count
        meta["_encrypted"] = bool(self.doc.is_encrypted)
        if self.path:
            try:
                meta["_file_size"] = os.path.getsize(self.path)
            except OSError:
                meta["_file_size"] = None
        return meta

    def set_metadata(self, **fields):
        self._auto_checkpoint("Edit metadata")

        """Set document metadata: title, author, subject, keywords, creator, producer."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        allowed = {"title", "author", "subject", "keywords", "creator", "producer"}
        meta = self.doc.metadata or {}
        for k, v in fields.items():
            if k in allowed:
                meta[k] = str(v) if v is not None else ""
        self.doc.set_metadata(meta)

    def extract_images(self, page_indices, out_dir, min_size=50, progress_cb=None):
        """Extract embedded images from pages into PNG files.

        Returns the list of written paths. min_size = minimum size in px.
        """
        if not self.doc:
            raise ValueError("No PDF is open.")
        if not os.path.isdir(out_dir):
            raise ValueError("Destination folder not found.")
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else "image"
        seen, written = set(), []
        total = len(page_indices)
        done = 0
        for pno in page_indices:
            page = self.doc.load_page(pno)
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(self.doc, xref)
                    if pix.width < min_size or pix.height < min_size:
                        pix = None
                        continue
                    if pix.colorspace and pix.colorspace.n > 3:  # CMYK dll -> RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.alpha:
                        pix = fitz.Pixmap(pix, 0)
                    out_path = os.path.join(out_dir, f"{base}_p{pno + 1}_{xref}.png")
                    pix.save(out_path)
                    written.append(out_path)
                    pix = None
                except Exception:
                    continue
            done += 1
            if progress_cb:
                progress_cb(done, total)
        return written

    def export_text(self, out_path, pages=None):
        """Export text of all / selected pages into a .txt file."""
        if not self.doc:
            raise ValueError("No PDF is open.")
        targets = pages if pages is not None else range(self.page_count)
        chunks = []
        for i in targets:
            if not (0 <= i < self.page_count):
                continue
            chunks.append(f"===== Page {i + 1} =====\n")
            chunks.append(self.doc.load_page(i).get_text("text").strip())
            chunks.append("\n\n")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(chunks))
        return out_path


# ---------- Standalone functions (no PDFTool object needed) ----------

def repair_pdf(src_path, dst_path):
    """Repair a broken PDF: open via MuPDF recovery mode, then save clean.

    Returns (ok: bool, message: str).
    """
    doc = None
    try:
        try:
            doc = fitz.open(src_path)
        except Exception:
            # Fall back to reading raw bytes; MuPDF can recover broken xref structures.
            with open(src_path, "rb") as f:
                raw = f.read()
            doc = fitz.open(stream=raw, filetype="pdf")
        n = doc.page_count
        doc.save(dst_path, garbage=4, deflate=True)
        return True, f"Successfully repaired ({n} pages): {dst_path}"
    except Exception as e:
        return False, f"Repair failed: {e}"
    finally:
        if doc:
            try:
                doc.close()
            except Exception:
                pass


def _line_text_near(page, rect, pad=2.0):
    """Text of the block containing ``rect`` - used to show search context."""
    probe = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    try:
        raw = page.get_text("blocks", clip=probe)
    except Exception:
        return ""
    for block in raw:
        text = (block[4] or "").strip().replace("\n", " ")
        if text:
            return text[:160]
    return ""