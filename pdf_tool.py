"""PDF Tool — entry point.

Sejak refactor, seluruh kode GUI ada di paket ``pdftool/``:

    pdftool/theme.py            design tokens (warna, font)
    pdftool/dialogs.py          picker file native (zenity)
    pdftool/widgets.py          HoverButton, SidebarItem
    pdftool/app.py              kerangka aplikasi (PDFToolApp + main)
    pdftool/actions_organize.py aksi organisasi halaman
    pdftool/actions_security.py watermark, password/unlock, repair
    pdftool/actions_export.py   ekspor image/text, compress, nomor halaman
    pdftool/actions_edit.py     edit teks, stempel, metadata
    pdftool/actions_convert.py  konversi image/office/html/word

Modul ini tinggal facade kompatibilitas: menjalankan ``python3 pdf_tool.py``
atau ``./run.sh`` tetap bekerja seperti dulu.
"""
from pdftool.app import PDFToolApp, main  # noqa: F401

# Re-ekspor nama-nama lama agar impor eksternal tetap punya target.
from pdftool.theme import *  # noqa: F401,F403
from pdftool.dialogs import *  # noqa: F401,F403
from pdftool.widgets import HoverButton, SidebarItem, _safe_grab, _set_app_icon  # noqa: F401

if __name__ == "__main__":
    main()
