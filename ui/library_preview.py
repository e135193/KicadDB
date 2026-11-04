"""Background preview of a selected installed KiCad library entry."""
from pathlib import Path
import queue
import threading

from lcsc_preview import footprint_preview, symbol_library_previews
from ui.cad_preview import CadPreview


def _read(events, version, path, symbol):
    try:
        if symbol is not None:
            scenes = symbol_library_previews(path, symbol)
        else:
            scenes = [footprint_preview(Path(path).read_text(encoding='utf-8-sig'))]
        events.put((version, scenes, None))
    except Exception as exc:
        events.put((version, [], str(exc)))


class LibraryPreview(CadPreview):
    def __init__(self, parent):
        super().__init__(parent, compact=True)
        self.load_events = queue.Queue()
        self.load_version = 0
        self.loading = False
        self.pending = None

    def show_file(self, path, symbol=None):
        self.load_version += 1
        self.pending = None
        self.set_scenes(message='Loading library preview…' if path else 'No library file is available for this selection.')
        if not path:
            return
        job = self.load_version, str(path), symbol
        if self.loading:
            self.pending = job
        else:
            self._start(job)

    def _start(self, job):
        self.loading = True
        threading.Thread(target=_read, args=(self.load_events, *job), daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            version, scenes, error = self.load_events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.loading = False
            if version == self.load_version:
                self.set_scenes(scenes, f'Preview unavailable: {error}' if error else 'No drawable geometry.')
            pending, self.pending = self.pending, None
            if pending:
                self._start(pending)
        super()._poll()
