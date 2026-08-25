"""PDF Tool - entry point.

Since the refactor, all GUI code lives in the ``pdftool/`` package:

    pdftool/theme.py            design tokens (colors, fonts)
    pdftool/dialogs.py          native file pickers (zenity)
    pdftool/widgets.py          HoverButton, SidebarItem
    pdftool/app.py              application shell (PDFToolApp + main)
    pdftool/actions_organize.py page organization actions
    pdftool/actions_security.py watermark, password/unlock, repair
    pdftool/actions_export.py   image/text export, compress, page numbers
    pdftool/actions_edit.py     text editing, stamps, metadata
    pdftool/actions_convert.py  image/office/html/word conversions

This module is just a compatibility facade: running ``python3 pdf_tool.py``
or ``./run.sh`` keeps working as before.
"""
from pdftool.app import PDFToolApp, main  # noqa: F401

# Re-export legacy names so external imports still have targets.
from pdftool.theme import *  # noqa: F401,F403
from pdftool.dialogs import *  # noqa: F401,F403
from pdftool.widgets import HoverButton, SidebarItem, _safe_grab, _set_app_icon  # noqa: F401

if __name__ == "__main__":
    main()
