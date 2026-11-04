"""Tk preview pane with background rasterization, fit, zoom and drag-to-pan."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from lcsc_preview import render_png


def _render_work(events, version, scene, size, zoom, pan):
    try:
        events.put((version, render_png(scene, *size, zoom, pan), None))
    except Exception as exc:
        events.put((version, None, str(exc)))


class CadPreview(ttk.Frame):
    def __init__(self, parent, compact=False):
        super().__init__(parent)
        self.scenes = []
        self.scene = None
        self.zoom = 1.0
        self.pan = (0, 0)
        self.photo = None
        self.version = 0
        self.busy = False
        self.closed = False
        self.events = queue.Queue()
        self.render_id = None
        self.message = 'Select a component to view its CAD drawing.'
        bar = ttk.Frame(self, padding=4)
        self.controls = bar
        bar.pack(fill='x')
        self.unit = ttk.Combobox(bar, state='readonly', width=8 if compact else 16)
        self.unit.pack(side='left', padx=(0,8))
        self.unit.bind('<<ComboboxSelected>>', self.select_unit)
        ttk.Button(bar, text='−', width=3, command=lambda: self.change_zoom(1/1.25)).pack(side='left')
        ttk.Button(bar, text='+', width=3, command=lambda: self.change_zoom(1.25)).pack(side='left', padx=4)
        ttk.Button(bar, text='Fit', width=5, command=self.fit).pack(side='left')
        self.summary = tk.StringVar(master=self)
        self.summary_label = ttk.Label(bar, textvariable=self.summary)
        self.summary_label.pack(side='left', padx=10)
        self.help_label = ttk.Label(self, text='Mouse wheel: zoom · Drag: pan · Double-click: fit', padding=4)
        self.help_label.pack(side='bottom', fill='x')
        if compact:
            self.summary_label.pack_forget()
            ttk.Label(self, textvariable=self.summary, padding=4, wraplength=280).pack(side='bottom', fill='x')
        self.canvas = tk.Canvas(self, background='white', highlightthickness=0, height=240)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self._resize)
        self.canvas.bind('<MouseWheel>', lambda e: self.change_zoom(1.25 if e.delta>0 else 1/1.25))
        self.canvas.bind('<Button-4>', lambda e: self.change_zoom(1.25))
        self.canvas.bind('<Button-5>', lambda e: self.change_zoom(1/1.25))
        self.canvas.bind('<ButtonPress-1>', self._start_drag)
        self.canvas.bind('<B1-Motion>', self._drag)
        self.canvas.bind('<Double-1>', lambda e: self.fit())
        self.bind('<Destroy>', self._destroyed, add='+')
        self.poll_id = self.after(60, self._poll)

    def set_scenes(self, scenes=(), message='Preview unavailable.'):
        self.version += 1
        self.scenes = list(scenes)
        self.photo = None
        self.canvas.delete('all')
        self.message = message
        self.unit.configure(values=[f'View {i+1}' for i in range(len(scenes))], state='readonly' if len(scenes)>1 else 'disabled')
        self.unit.set('View 1' if scenes else '')
        self.scene = scenes[0] if scenes else None
        self.fit()

    def select_unit(self, event=None):
        if self.scenes:
            self.scene = self.scenes[self.unit.current()]
            self.fit()

    def fit(self):
        self.zoom, self.pan = 1.0, (0,0)
        self._schedule()

    def change_zoom(self, factor):
        if self.scene:
            previous = self.zoom
            self.zoom = min(12, max(.25, self.zoom*factor))
            self.pan = tuple(v*self.zoom/previous for v in self.pan)
            self._schedule()
        return 'break'

    def _start_drag(self, event):
        self.drag_start = (event.x,event.y,self.pan)

    def _drag(self, event):
        if self.scene and hasattr(self,'drag_start'):
            x,y,pan = self.drag_start
            self.pan = (pan[0]+event.x-x, pan[1]+event.y-y)
            self._schedule()

    def _resize(self, event):
        self.help_label.configure(wraplength=max(150, event.width-12))
        self._schedule()

    def _schedule(self):
        if self.closed:
            return
        self.version += 1
        self.summary.set(f'{self.scene.summary} · {self.zoom:.0%}' if self.scene else '')
        if self.render_id:
            self.after_cancel(self.render_id)
        self.render_id = self.after(70, self._render)

    def _render(self):
        self.render_id = None
        if self.closed:
            return
        width,height = self.canvas.winfo_width(),self.canvas.winfo_height()
        if not self.scene:
            self.canvas.delete('all')
            self.canvas.create_text(width/2,height/2,text=self.message,fill='#667085',width=max(100,width-40))
            return
        if self.busy or width<10 or height<10:
            return
        self.busy = True
        threading.Thread(target=_render_work,
                         args=(self.events,self.version,self.scene,(width,height),self.zoom,self.pan), daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            version,png,error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if version == self.version:
                self.canvas.delete('all')
                if error:
                    self.canvas.create_text(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2,
                                            text=f'Preview unavailable: {error}',width=max(100,self.canvas.winfo_width()-40),fill='#667085')
                else:
                    self.photo = tk.PhotoImage(master=self.canvas,data=png)
                    self.canvas.create_image(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2,image=self.photo)
            else:
                self._render()
        self.poll_id = self.after(60,self._poll)

    def _destroyed(self,event):
        if event.widget is self:
            self.closed = True
            self.version += 1
            self.after_cancel(self.poll_id)
            if self.render_id:
                self.after_cancel(self.render_id)
            self.photo = None
