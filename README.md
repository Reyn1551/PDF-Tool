# PDF Tool 📄

Aplikasi PDF desktop (Tkinter + PyMuPDF) dengan fitur lengkap gaya iLovePDF — merge,
split, compress, konversi, edit teks, proteksi, dan lainnya. Berjalan langsung di Linux.

## 🚀 Cara Menjalankan (tanpa `python3 bla bla`)

### Cara 1 — Klik dua kali / satu perintah
```bash
./run.sh
```
Script ini otomatis: cek Python → pasang dependensi bila belum ada → jalankan aplikasi.
Bisa juga **double-click** `run.sh` dari file manager (klik kanan → *Run*/Execute).

### Cara 2 — Pasang ke menu aplikasi (sekali saja)
```bash
./install.sh          # tambahkan "PDF Tool" ke menu aplikasi + ikon
./install.sh --remove # hapus lagi
```

## ✨ Fitur

| Kategori | Fitur |
|---|---|
| **Organisasi** | Hapus halaman · Sisip dari PDF lain · Merge · Split (rentang/bebas/banyak rentang) · Rotasi · Urut ulang · Balik urutan · Crop/potong tepi |
| **Konversi** | PDF → Image (PNG/JPG, pilih DPI) · Image → PDF (auto/A4) · Office → PDF (Word/Excel/PPT via LibreOffice) · PDF → Word (.docx) · HTML → PDF · PDF → Text |
| **Edit** | Edit teks digital presisi (font/warna/ukuran asli) · Tambah/timpa teks untuk PDF scan · Whiteout area · Stempel / tanda tangan gambar · Metadata dokumen |
| **Keamanan & Optimasi** | Watermark teks/gambar · Password (AES-256) · Unlock password · Repair PDF rusak · Compress · Nomor halaman · Ekstrak gambar tertanam |

Preview thumbnail semua halaman, pilih banyak halaman sekaligus (klik/klik-semua),
scroll rodol mouse di mana pun, dialog password 3x percobaan.

## 📦 Dependensi

Inti (dipasang otomatis oleh `run.sh`):
- Python 3.8+ dengan `tkinter` (`sudo apt install python3-tk`)
- PyMuPDF

Opsional:
- Pillow, python-docx — ikon & PDF→Word
- LibreOffice (`sudo apt install libreoffice`) — Office→PDF
- zenity — dialog file native (fallback otomatis ke tkinter)

## 🧪 Test

```bash
python3 tests/test_edit_text.py        # 52 checks
python3 tests/test_new_features.py     # 24 checks (bugfix regresi + fitur baru)
python3 tests/test_pdf_to_image.py     # 13 checks
python3 tests/test_pdf_to_image_gui.py # 15 checks (integrasi GUI)
```

## 🗂️ Struktur

```
pdf_tool.py           Entry point (facade kompatibilitas)
run.sh                Peluncur cepat
install.sh            Installer pintasan menu aplikasi
assets/icon.png       Ikon aplikasi

pdftool/              Paket GUI (hasil refactor modular)
├── theme.py            Design tokens — warna & font
├── dialogs.py          Picker file native (zenity + fallback tkinter)
├── widgets.py          HoverButton, SidebarItem
├── app.py              Kerangka aplikasi: layout, preview, status
├── actions_organize.py Buka/hapus/merge/split/rotasi/urut/crop
├── actions_security.py Watermark, password/unlock, repair
├── actions_export.py   Ekspor image/text, compress, nomor halaman
├── actions_edit.py     Edit teks digital & scan, stempel, metadata
└── actions_convert.py  Image/Office/HTML→PDF, PDF→Word

pdf_ops.py            Operasi inti PDF murni PyMuPDF
pdf_convert.py        Konversi antar-format (images/office/html/docx)
tests/                Test suite (exit code 0 = lolos)
```

Menjalankan aplikasi: `./run.sh`, `python3 pdf_tool.py`, atau `python3 -m pdftool`.
