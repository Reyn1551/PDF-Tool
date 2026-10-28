"""Optional drag & drop support for PDF Tool.

Tkinter has no native XDND support, so this module bridges the ``tkinterdnd2``
package when it is installed and degrades silently when it is not - dropping a
file on the window is a convenience, never a hard dependency.

Usage from the shell::

    app.root = enable_drop(app.root, app.open_path)
"""
import os


def enable_drop(root, on_paths, on_message=None, widgets=()):
    """Register ``on_paths(list[str])`` as the drop handler if possible.

    Returns the (possibly replaced) root window: ``tkinterdnd2`` requires its
    own ``Tk`` subclass, so a plain ``tk.Tk`` cannot register drops without it.
    """
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except Exception:
        if on_message:
            on_message("Drag & drop needs the optional 'tkinterdnd2' package.")
        return root

    if not isinstance(root, TkinterDnD.Tk):
        return root

    def _dropped(event):
        paths = _split_drop(event.data)
        if paths:
            on_paths(paths)

    for widget in (root, *widgets):
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", _dropped)
        except Exception:
            continue
    return root


def _split_drop(data):
    """Split Tk's space separated drop payload, honouring ``{}`` quoting."""
    items, buffer, braced = [], "", False
    for ch in str(data or ""):
        if ch == "{":
            braced = True
        elif ch == "}":
            braced = False
        elif ch == " " and not braced:
            if buffer:
                items.append(buffer)
            buffer = ""
        else:
            buffer += ch
    if buffer:
        items.append(buffer)
    return [p for p in items if os.path.isfile(p)]


def create_root():
    """A ``Tk`` root that supports drops when ``tkinterdnd2`` is available."""
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk()
    except Exception:
        import tkinter as tk
        return tk.Tk()