"""Background job runner: keeps the Tk main thread free while PDFs are processed.

Why this exists
---------------
Tkinter is single threaded.  Calling PyMuPDF on the main thread blocks the
event loop, so the window stops repainting and the WM marks the app as "not
responding" during compression, conversion or large exports.

This module moves such work to a worker thread and marshals every callback
back to the Tk thread with ``after(0, ...)``.  Rules for callbacks:

* ``fn(job)`` runs on a worker thread.  It MUST NOT touch Tk objects, widgets
  or a ``fitz.Document`` that the UI is rendering.  Pass bytes/paths in and
  plain data out.
* ``on_done(result)``, ``on_error(exc)``, ``on_progress(text, fraction)`` and
  ``on_cancel()`` run on the Tk thread and may touch widgets freely.
* ``job.progress(...)`` is safe from the worker thread and throttled.
* ``job.check()`` raises :class:`JobCancelled` so a long loop can unwind.

A cancelled job reports through ``on_cancel`` - never as an error.
"""
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk

from .theme import BG, SURFACE, BORDER, TEXT, TEXT_MUTED, FONT_SMALL, FONT_BOLD


class JobCancelled(Exception):
    """Raised inside worker code when the user cancelled the job."""


class Job:
    """Handle for one background operation."""

    _PROGRESS_INTERVAL = 0.05  # seconds between UI progress updates

    def __init__(self, runner, name, title):
        self.runner = runner
        self.name = name
        self.title = title
        self.cancelled = False
        self.finished = False
        self.result = None
        self.error = None
        self.started = time.monotonic()
        self.dialog = None
        self._cancel_event = threading.Event()
        self._last_progress = 0.0

    # ---- worker-side API -------------------------------------------------
    def progress(self, text=None, fraction=None):
        """Report progress from the worker thread (throttled)."""
        now = time.monotonic()
        if now - self._last_progress < self._PROGRESS_INTERVAL:
            return
        self._last_progress = now
        self.runner._events.put(("progress", self, text, fraction))

    def check(self):
        """Abort if cancellation was requested."""
        if self._cancel_event.is_set():
            raise JobCancelled()

    @property
    def cancel_requested(self):
        return self._cancel_event.is_set()

    # ---- main-thread API -------------------------------------------------
    def cancel(self):
        self.cancelled = True
        self._cancel_event.set()

    def elapsed(self):
        return time.monotonic() - self.started


class ProgressDialog(tk.Toplevel):
    """Non-blocking progress window with an optional Cancel button."""

    def __init__(self, master, title, on_cancel=None, cancellable=True):
        super().__init__(master)
        self.on_cancel = on_cancel
        self.cancellable = cancellable
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._bar_mode = None

        tk.Label(self, text=title, font=FONT_BOLD, bg=BG, fg=TEXT, anchor="w").pack(
            fill=tk.X, padx=18, pady=(16, 2))
        self.message = tk.Label(self, text="Preparing...", font=FONT_SMALL, bg=BG,
                                fg=TEXT_MUTED, anchor="w", wraplength=320, justify=tk.LEFT)
        self.message.pack(fill=tk.X, padx=18, pady=(0, 8))

        self.bar = ttk.Progressbar(self, mode="indeterminate", length=320)
        self.bar.pack(fill=tk.X, padx=18)
        self.bar.start(40)
        self._bar_mode = "indeterminate"

        row = tk.Frame(self, bg=BG)
        row.pack(fill=tk.X, padx=18, pady=(8, 14))
        self.elapsed = tk.Label(row, text="0.0s", font=FONT_SMALL, bg=BG, fg=TEXT_MUTED)
        self.elapsed.pack(side=tk.LEFT)

        if cancellable:
            self.btn = tk.Button(
                row, text="Cancel", font=FONT_SMALL, bg=SURFACE, fg=TEXT, relief=tk.FLAT,
                highlightthickness=1, highlightbackground=BORDER, activebackground=BORDER,
                padx=12, pady=2, cursor="hand2", command=self._cancel)
            self.btn.pack(side=tk.RIGHT)

        self._center_on(master)
        self.bind("<Escape>", lambda e: self._cancel())

    def _center_on(self, master):
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            x = master.winfo_rootx() + max(0, (master.winfo_width() - w) // 2)
            y = master.winfo_rooty() + max(0, (master.winfo_height() - h) // 3)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _cancel(self):
        if not self.cancellable:
            return
        self.message.config(text="Cancelling - finishing the current step...")
        if hasattr(self, "btn"):
            try:
                self.btn.config(state=tk.DISABLED)
            except tk.TclError:
                pass
        if self.on_cancel:
            self.on_cancel()

    def set(self, text=None, fraction=None):
        try:
            if text:
                self.message.config(text=text)
            if fraction is None:
                if self._bar_mode != "indeterminate":
                    self.bar.stop()
                    self.bar.config(mode="indeterminate")
                    self.bar.start(40)
                    self._bar_mode = "indeterminate"
            else:
                if self._bar_mode != "determinate":
                    self.bar.stop()
                    self.bar.config(mode="determinate", maximum=100)
                    self._bar_mode = "determinate"
                self.bar["value"] = max(0.0, min(1.0, float(fraction))) * 100
        except tk.TclError:
            pass

    def tick(self, seconds):
        try:
            self.elapsed.config(text=f"{seconds:.1f}s")
        except tk.TclError:
            pass

    def close(self):
        try:
            self.bar.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class JobRunner:
    """Thread pool of one-or-more workers that reports back to Tk safely."""

    DIALOG_DELAY = 0.20   # show the progress window only for slow jobs
    POLL_MS = 40

    def __init__(self, tk_widget, max_concurrent=3):
        self._widget = tk_widget
        self._events = queue.Queue()
        self._jobs = []
        self._slots = threading.Semaphore(max(1, int(max_concurrent)))
        self._alive = True
        self._poll_id = tk_widget.after(self.POLL_MS, self._poll)

    # ---- submission ------------------------------------------------------
    def submit(self, name, fn, on_done=None, on_error=None, on_progress=None,
               on_cancel=None, title=None, cancellable=True):
        """Start ``fn(job)`` on a worker thread.

        ``title`` -> show a progress dialog with that title once the job runs
        longer than :attr:`DIALOG_DELAY`.  Pass ``title=None`` for silent jobs
        (e.g. thumbnail rendering).
        """
        job = Job(self, name, title)
        job.on_done = on_done
        job.on_error = on_error
        job.on_progress = on_progress
        job.on_cancel = on_cancel
        job.cancellable = cancellable
        job.fn = fn
        self._jobs.append(job)
        threading.Thread(target=self._run, args=(job,), daemon=True,
                         name=f"pdfjob-{name}").start()
        return job

    @property
    def busy(self):
        return bool(self._jobs)

    def shutdown(self):
        """Cancel everything and stop polling (app close / tests)."""
        self._alive = False
        for job in list(self._jobs):
            job.cancel()
        try:
            self._widget.after_cancel(self._poll_id)
        except Exception:
            pass

    def wait(self, timeout=60.0):
        """Pump the event loop until all jobs finish (headless tests only)."""
        deadline = time.monotonic() + timeout
        while self._jobs and time.monotonic() < deadline:
            try:
                self._widget.update()
            except tk.TclError:
                break
            time.sleep(0.01)
        return not self._jobs

    # ---- worker ----------------------------------------------------------
    def _run(self, job):
        with self._slots:
            if job.cancelled:
                self._events.put(("cancel", job, None, None))
                return
            try:
                result = job.fn(job)
            except JobCancelled:
                self._events.put(("cancel", job, None, None))
            except BaseException as exc:  # noqa: BLE001 - report, never die silently
                traceback.print_exc()
                self._events.put(("error", job, exc, None))
            else:
                if job.cancelled:
                    self._events.put(("cancel", job, None, None))
                else:
                    self._events.put(("done", job, result, None))

    # ---- main thread -----------------------------------------------------
    def _poll(self):
        if not self._alive:
            return
        try:
            self._open_due_dialogs()
            self._drain()
        finally:
            try:
                self._poll_id = self._widget.after(self.POLL_MS, self._poll)
            except tk.TclError:
                self._alive = False

    def _open_due_dialogs(self):
        for job in self._jobs:
            if job.dialog is not None or not job.title or job.finished:
                continue
            if job.elapsed() < self.DIALOG_DELAY:
                continue
            try:
                job.dialog = ProgressDialog(
                    self._widget.winfo_toplevel(), job.title,
                    on_cancel=job.cancel, cancellable=job.cancellable)
            except tk.TclError:
                job.dialog = None

    def _drain(self):
        while True:
            try:
                kind, job, a, b = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if job.dialog is not None:
                    job.dialog.set(a, b)
                if job.on_progress:
                    self._safe_call(job.on_progress, a, b)
                continue

            job.finished = True
            if job.dialog is not None:
                job.dialog.close()
                job.dialog = None
            if job in self._jobs:
                self._jobs.remove(job)

            if kind == "done":
                job.result = a
                if job.on_done:
                    self._safe_call(job.on_done, a)
            elif kind == "error":
                job.error = a
                if job.on_error:
                    self._safe_call(job.on_error, a)
                else:
                    import tkinter.messagebox as mb
                    mb.showerror(job.name or "Error", f"{a}")
            else:
                if job.on_cancel:
                    self._safe_call(job.on_cancel)

        for job in self._jobs:
            if job.dialog is not None:
                job.dialog.tick(job.elapsed())

    @staticmethod
    def _safe_call(fn, *args):
        """A broken callback must not kill the poll loop."""
        try:
            fn(*args)
        except tk.TclError:
            pass
        except Exception:
            traceback.print_exc()


def progress_bridge(job, label=None):
    """Adapt backend callbacks to ``job.progress``.

    Backends call ``progress(done, total)`` or ``progress(done, total, name)``;
    ``Job.progress`` wants ``(text, fraction)``.  Keeping the translation here
    means every action mixin gets the same wording and clamping.
    """
    def report(done, total=None, name=None):
        fraction = None
        if total:
            fraction = min(1.0, max(0.0, float(done) / float(total)))
        text = label or ""
        if name:
            text = f"{text}: {name}" if text else str(name)
        elif total:
            text = f"{text} {done}/{total}".strip()
        job.progress(text or None, fraction)

    return report


def text_bridge(job):
    """Adapt a backend ``progress(text)`` callback to ``job.progress``."""
    def report(text, fraction=None):
        job.progress(str(text) if text else None, fraction)
    return report


# ---- small helpers used by the action mixins -----------------------------


def run_job(app, title, fn, on_done=None, on_error=None, on_progress=None,
            name="pdf-task", cancellable=True):
    """Submit ``fn(job)`` on the app's runner and route errors to a messagebox."""
    root = app.root
    if on_error is None:
        def on_error(exc):
            import tkinter.messagebox as mb
            mb.showerror(title, f"{exc}", parent=root)
    return app.jobs.submit(name, fn, on_done=on_done, on_error=on_error,
                           on_progress=on_progress, title=title,
                           cancellable=cancellable)