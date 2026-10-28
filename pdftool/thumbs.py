"""Thumbnail rendering that never blocks the Tk main thread.

Rendering every page synchronously is what makes a PDF viewer feel broken: the
event loop stops and the window greys out.  Here:
  * page rasterisation runs on one worker thread (PPM bytes, because Tk's
    ``PhotoImage`` must be built on the main thread);
  * results land in a bounded LRU keyed by document generation, so scrolling up
    again is instant and an edit invalidates only stale bitmaps;
  * requests are deduplicated, so fast scrolling never spawns extra threads.
"""
import threading
from collections import OrderedDict
from queue import Empty, Queue


def render_ppm(doc, index, max_width=140, lock=None):
    """Rasterise one page to raw PPM bytes.

    ``doc`` may be shared with other renderers, so the load/pixmap pair runs
    under ``lock`` (``PDFTool.lock``).
    """
    import fitz

    def _render():
        page = doc.load_page(index)
        rect = page.rect
        w = rect.width or 1.0
        h = rect.height or 1.0
        scale = max_width / w
        if h * scale > max_width * 2.2:      # very tall page: bound the height too
            scale = (max_width * 2.2) / h
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pix.tobytes("ppm"), pix.width, pix.height

    if lock is None:
        return _render()
    with lock:
        return _render()


class ThumbCache:
    """Bounded LRU: ``(generation, index, width) -> (ppm, w, h)``."""

    def __init__(self, limit=800):
        self.limit = max(8, int(limit))
        self._data = OrderedDict()

    @staticmethod
    def key(generation, index, width):
        return (generation, index, int(width))

    def get(self, key):
        try:
            value = self._data.pop(key)
        except KeyError:
            return None
        self._data[key] = value           # refresh recency
        return value

    def put(self, key, value):
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.limit:
            self._data.popitem(last=False)

    def invalidate(self, keep_generation=None):
        if keep_generation is None:
            self._data.clear()
            return
        for k in [k for k in self._data if k[0] != keep_generation]:
            self._data.pop(k, None)

    def __len__(self):
        return len(self._data)


class ThumbLoader:
    """One worker thread + main-thread delivery for page thumbnails."""

    POLL_MS = 16

    def __init__(self, root, cache=None, max_width=140):
        self.root = root
        self.cache = cache or ThumbCache()
        self.max_width = max_width
        self._generation = 0
        self._requests = Queue()
        self._results = Queue()
        self._callbacks = {}       # (generation, index) -> [callback, ...]
        self._pending = set()
        self._stopped = False
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="pdftool-thumbs")
        self._worker.start()
        self._poll_id = root.after(self.POLL_MS, self._drain)

    # -- API ---------------------------------------------------------------
    @property
    def generation(self):
        return self._generation

    def begin(self, generation=None):
        """Start a new document generation; stale bitmaps and in-flight jobs die."""
        self._generation = self._generation + 1 if generation is None else int(generation)
        self.cache.invalidate(self._generation)
        self._pending.clear()
        self._callbacks.clear()
        return self._generation

    def cached(self, index, width=None):
        width = self.max_width if width is None else int(width)
        return self.cache.get(self.cache.key(self._generation, index, width))

    def request(self, doc, index, callback, lock=None, width=None):
        """Request a thumbnail at ``width`` px.

        The cache key includes the width, so zooming in does not reuse a blurry
        bitmap and zooming back out stays instant.  Returns True when the
        callback ran synchronously from cache.
        """
        width = self.max_width if width is None else int(width)
        hit = self.cached(index, width)
        if hit is not None:
            callback(index, *hit)
            return True
        key = (self._generation, index, width)
        self._callbacks.setdefault(key, []).append(callback)
        if key not in self._pending:
            self._pending.add(key)
            self._requests.put((key, doc, lock, index, width))
        return False

    def stop(self):
        self._stopped = True
        try:
            self.root.after_cancel(self._poll_id)
        except Exception:
            pass

    def wait_idle(self, timeout=10.0):
        """Pump the event loop until no renders are in flight (tests/smoke)."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pending:
                return True
            try:
                self.root.update()
            except Exception:
                return False
            time.sleep(0.005)
        return not self._pending

    # -- worker ------------------------------------------------------------
    def _run(self):
        while not self._stopped:
            try:
                key, doc, lock, index, width = self._requests.get(timeout=0.2)
            except Empty:
                continue
            if key[0] != self._generation:
                continue
            try:
                ppm, w, h = render_ppm(doc, index, width, lock)
            except Exception:
                ppm, w, h = None, 0, 0
            self._results.put((key, ppm, w, h))

    # -- main thread -------------------------------------------------------
    def _drain(self):
        if self._stopped:
            return
        while True:
            try:
                key, ppm, w, h = self._results.get_nowait()
            except Empty:
                break
            callbacks = self._callbacks.pop(key, [])
            self._pending.discard(key)
            if ppm is None or key[0] != self._generation:
                continue
            self.cache.put(self.cache.key(key[0], key[1], key[2]), (ppm, w, h))
            for cb in callbacks:
                try:
                    cb(key[1], ppm, w, h)
                except Exception:
                    pass
        try:
            self._poll_id = self.root.after(self.POLL_MS, self._drain)
        except Exception:
            self._stopped = True