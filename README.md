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
| **Convert** | PDF → Image (PNG/JPG, choose DPI) · Image → PDF (auto/A4) · Office → PDF (Word/Excel/PPT via LibreOffice) · PDF → Word (.docx) · HTML → PDF · PDF → Text |
| **Edit** | Precise digital text editing (original font/color/size) · Add/overwrite text for scanned PDFs · Whiteout areas · Image stamps / signatures · Document metadata |
| **Security & Optimization** | Text/image watermark · Password protection (AES-256) · Unlock password · Repair broken PDFs · Compress · Page numbers · Extract embedded images |

Thumbnail previews of every page, multi-page selection (click / select all),
mouse-wheel scrolling anywhere, 3-attempt password dialog.

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
python3 tests/test_edit_text.py        # 52 checks
python3 tests/test_new_features.py     # 24 checks (bug regression + new features)
python3 tests/test_pdf_to_image.py     # 13 checks
python3 tests/test_pdf_to_image_gui.py # 15 checks (GUI integration)
```

## 🗂️ Structure

```
pdf_tool.py           Entry point (compatibility facade)
run.sh                Quick launcher
install.sh            Application-menu shortcut installer
assets/icon.png       Application icon

pdftool/              GUI package (modular refactor)
├── theme.py            Design tokens - colors & fonts
├── dialogs.py          Native file pickers (zenity + tkinter fallback)
├── widgets.py          HoverButton, SidebarItem
├── app.py              Application shell: layout, preview, status
├── actions_organize.py Open/delete/merge/split/rotate/reorder/crop
├── actions_security.py Watermark, password/unlock, repair
├── actions_export.py   Export image/text, compress, page numbers
├── actions_edit.py     Digital & scanned text editing, stamps, metadata
└── actions_convert.py  Image/Office/HTML→PDF, PDF→Word

pdf_ops.py            Core PDF operations, pure PyMuPDF
pdf_convert.py        Format conversions (images/office/html/docx)
tests/                Test suite (exit code 0 = pass)
```

Run the app with: `./run.sh`, `python3 pdf_tool.py`, or `python3 -m pdftool`.
