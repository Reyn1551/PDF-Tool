# PDF Tool 📄

A desktop PDF application (Tkinter + PyMuPDF) with a full iLovePDF-style feature
set — merge, split, compress, convert, edit text, protect, and more. Runs natively
on Linux.

## 🚀 How to Run (no `python3 blah blah` needed)

### Option 1 - Double-click / single command
```bash
./run.sh
```
The script automatically: checks Python → installs dependencies if missing →
launches the app. You can also **double-click** `run.sh` in your file manager
(right-click → Run/Execute).

### Option 2 - Install into the application menu (once)
```bash
./install.sh          # adds "PDF Tool" to the application menu + icon
./install.sh --remove # removes it again
```

## ✨ Features

| Category | Tools |
|---|---|
| **Organize** | Delete pages · Insert from another PDF · Merge · Split (range/free/multi-range) · Rotate · Reorder · Reverse order · Crop pages |
| **Page tools** | Extract pages to a new PDF · Split into N-page chunks · N-up imposition · Duplex booklet · Find & remove blank pages · Auto-trim margins · Document summary (pages/words/images) |
| **Convert** | PDF → Image (PNG/JPG, choose DPI) · Image → PDF (auto/A4) · Office → PDF (Word/Excel/PPT via LibreOffice) · PDF → Word (.docx) · HTML → PDF · PDF → Text |
| **Advanced convert** | PDF → Markdown · Page → SVG · Web optimize (downsample + split at a size budget) · Batch-convert a folder · Word & image counts |
| **Edit** | Precise digital text editing (original font/color/size) · Add/overwrite text for scanned PDFs · Whiteout areas · Image stamps / signatures · Document metadata |
| **Redact & sanitize** | Permanent text redaction (true removal, not a black box) · Sanitize (metadata, scripts, embedded files, annotations) · Attachments: list / add / extract / remove |
| **Security & Optimization** | Text/image watermark · Password protection (AES-256) · Unlock password · Repair broken PDFs · Compress (quality presets or exact target size in MB, quality-floor guarded) · Page numbers · Extract embedded images |

### Desktop ergonomics

- **Command palette** (`Ctrl+K` / `Ctrl+Shift+P`): every action, fuzzy-searchable.
- **Find in document** (`Ctrl+F` / `F3`): range-limited search, `Enter` walks the hits.
- **Undo / redo** (`Ctrl+Z`, `Ctrl+Shift+Z`) with named history steps and an
  unsaved-changes guard on open/quit.
- **Recent files**, drag-and-drop a PDF onto the window, `Ctrl+O` / `Ctrl+S`.
- **Page grid**: click to select, shift-click for a range, `Ctrl+A` for all,
  `Ctrl+ +` / `Ctrl+-` / `Ctrl+0` to zoom.
- **Virtualised preview**: a 1000-page file builds ~20 thumbnail cards, so
  opening and scrolling stay instant; every long operation (compress, convert,
  redact, export, merge…) runs on a worker thread with progress + cancel.
- **Automatic undo checkpoints** and atomic batches: a failed multi-step edit
  rolls back instead of leaving a half-modified document.

## 📦 Dependencies

Core (installed automatically by `run.sh`):
- Python 3.8+ with `tkinter` (`sudo apt install python3-tk`)
- PyMuPDF

Optional:
- Pillow, python-docx — app icon & PDF→Word
- LibreOffice (`sudo apt install libreoffice`) — Office→PDF
- zenity — native file dialogs (automatic tkinter fallback)

## 🧪 Tests

```bash
for t in tests/test_*.py; do python3 "$t" || exit 1; done   # whole suite
```

| File | Checks | Covers |
|---|---|---|
| `test_core_stability.py` | 38 | undo/redo, snapshots, search, LRU thumb cache, job runner + cancel, recent/drop helpers |
| `test_edit_text.py` | 52 | digital & scanned text editing, whiteout, fonts |
| `test_new_features.py` | 24 | crop, reverse, watermark, metadata, repair and other regressions |
| `test_compress.py` | 16 | compression engine, target size, quality floor |
| `test_pdf_to_image.py` | 13 | PDF → image rendering |
| `test_pdf_to_image_gui.py` | 15 | export dialog + job integration |
| `test_slices_pages.py` | 53 | extract/split/N-up/booklet/blank pages/trim/stats |
| `test_slices_security.py` | 41 | redaction, sanitize, attachments |
| `test_convert_extra.py` | 45 | Markdown, SVG, web optimize, batch, counts |
| `test_virtualization_gui.py` | 15 | bounded card set, recycling, selection & search jumps |

## 🗂️ Structure

```
pdf_tool.py           Entry point (compatibility facade)
run.sh                Quick launcher
install.sh            Application-menu shortcut installer
assets/icon.png       Application icon

pdftool/                 GUI package
├── theme.py               Design tokens - colors & fonts
├── dialogs.py             Native file pickers (zenity + tkinter fallback)
├── widgets.py             HoverButton, SidebarItem
├── app.py                 Shell: layout, virtualised grid, menus, shortcuts
├── jobs.py                Background JobRunner + progress dialog (cancel-safe)
├── thumbs.py              Threaded thumbnail rendering + LRU cache
├── search.py              Find-in-document panel (worker search on big files)
├── palette.py             Ctrl+K command palette
├── recent.py              Recent-files store
├── dnd.py                 Drag-and-drop handling
├── actions_organize.py    Open/delete/merge/split/rotate/reorder/crop
├── actions_pages_extra.py Extract/chunks/N-up/booklet/blank pages/trim/stats
├── actions_security.py    Watermark, password/unlock, repair
├── actions_export.py      Export image/text, compress, page numbers
├── actions_edit.py        Digital & scanned text editing, stamps, metadata
├── actions_convert.py     Image/Office/HTML→PDF, PDF→Word
├── actions_convert_extra.py Markdown, SVG, web optimize, batch, counts
└── actions_redact.py      Redaction, sanitize, attachments

pdf_ops.py             Core document model: undo/redo, batches, search, export
pdf_ops_pages.py       Page-level operations (extract, split, N-up, booklet…)
pdf_ops_security.py    Redaction, sanitize, attachment helpers
pdf_convert.py         Format conversions (images/office/html/docx)
pdf_convert_extra.py   Markdown, SVG, web optimize, stats
tests/                 Test suite (exit code 0 = pass)
```

Run the app with: `./run.sh`, `python3 pdf_tool.py`, or `python3 -m pdftool`.
