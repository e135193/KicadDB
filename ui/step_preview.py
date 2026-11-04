"""Interactive STEP tab using background CAD loading and rasterization."""
import queue
import threading
from tkinter import filedialog, ttk

from step_preview import load_component_step, read_step, render_step
from ui.cad_preview import CadPreview


def _load_work(events, version, operation):
    try:
        events.put((version, operation(), None))
    except Exception as exc:
        events.put((version, None, str(exc)))


def _render_work(events, version, mesh, size, zoom, pan, angles):
    try:
        events.put((version, render_step(mesh, *size, zoom, pan, angles), None))
    except Exception as exc:
        events.put((version, None, str(exc)))


class StepPreview(CadPreview):
    def __init__(self, parent, get_product, get_folder):
        super().__init__(parent)
        self.get_product = get_product
        self.get_folder = get_folder
        self.angles = (-35, 60)
        self.load_version = 0
        self.load_events = queue.Queue()
        self.load_busy = False
        self.pending_load = None
        self.loaded_code = None
        self.requested_code = None
        self.unit.pack_forget()
        self.summary_label.pack_forget()
        ttk.Label(self, textvariable=self.summary, padding=4, wraplength=900).pack(side='bottom', fill='x')
        file_bar = ttk.Frame(self)
        file_bar.pack(before=self.canvas, fill='x', padx=4)
        self.load_button = ttk.Button(file_bar, text='Load 3D model', command=self.load_component)
        self.load_button.pack(side='left', padx=4)
        ttk.Button(file_bar, text='Open STEP…', command=self.open_step).pack(side='left', padx=4)
        for label, angles in (('Top', (0, 0)), ('Front', (0, 90)), ('Iso', (-35, 60))):
            ttk.Button(self.controls, text=label, width=5,
                       command=lambda a=angles: self.set_view(a)).pack(side='left', padx=2)
        self.help_label.configure(text='Drag: rotate · Right-drag: pan · Mouse wheel: zoom · Double-click: fit · STEP geometry')
        self.canvas.bind('<ButtonPress-3>', lambda event: CadPreview._start_drag(self, event))
        self.canvas.bind('<B3-Motion>', lambda event: CadPreview._drag(self, event))
        self.set_scenes(message='Select a component, then click Load 3D model; or open a STEP file.')

    def reset_component(self):
        self.load_version += 1
        self.pending_load = None
        self.loaded_code = None
        self.requested_code = None
        self.angles = (-35, 60)
        self.set_scenes(message='Select the 3D Model tab or click Load 3D model to preview the component.')

    def load_component(self, force=True):
        product = self.get_product()
        if not product:
            self.set_scenes(message='Select a component and wait for its details to load.')
            return
        code = product['lcsc_id']
        if not force and (self.loaded_code == code or self.requested_code == code):
            return
        folder = self.get_folder()
        self._load(lambda: load_component_step(product, folder), code)

    def open_step(self):
        filename = filedialog.askopenfilename(parent=self, title='Open STEP model',
                                             filetypes=[('STEP models', '*.step *.stp'), ('All files', '*.*')])
        if filename:
            self._load(lambda: read_step(filename), None)

    def _load(self, operation, code):
        self.load_version += 1
        self.loaded_code = None
        self.requested_code = code
        self.set_scenes(message='Loading STEP model…')
        job = (self.load_version, operation, code)
        if self.load_busy:
            self.pending_load = job
            return
        self._start_load(job)

    def _start_load(self, job):
        version, operation, code = job
        self.load_busy = True
        self.active_code = code
        threading.Thread(target=_load_work, args=(self.load_events, version, operation), daemon=True).start()

    def set_view(self, angles):
        self.angles = angles
        self.fit()

    def _start_drag(self, event):
        self.orbit_start = (event.x, event.y, self.angles)

    def _drag(self, event):
        if self.scene and hasattr(self, 'orbit_start'):
            x, y, angles = self.orbit_start
            self.angles = ((angles[0] + (event.x - x) * .6) % 360,
                           (angles[1] + (event.y - y) * .6) % 360)
            self._schedule()

    def _render(self):
        if not self.scene:
            return super()._render()
        self.render_id = None
        if self.closed:
            return
        size = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.busy or min(size) < 10:
            return
        self.busy = True
        threading.Thread(target=_render_work,
                         args=(self.events, self.version, self.scene, size, self.zoom, self.pan, self.angles),
                         daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            version, mesh, error = self.load_events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.load_busy = False
            if version == self.load_version:
                self.requested_code = None
                self.loaded_code = self.active_code if mesh is not None else None
                self.angles = (-35, 60)
                self.set_scenes([mesh] if mesh is not None else [], f'3D preview unavailable: {error}')
            pending, self.pending_load = self.pending_load, None
            if pending:
                self._start_load(pending)
        super()._poll()
