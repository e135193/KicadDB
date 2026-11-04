"""Live obsolete component report, including historical project usages."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import product_tree as model
from ui.product_tree_window import table


class ObsoleteProductsWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('Obsolete Products')
        self.geometry('1320x600')
        self.minsize(850, 400)
        self.jobs = queue.Queue()
        self.busy = False
        self.records = []
        self.summary = tk.StringVar()
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill=tk.X)
        self.refresh_button = ttk.Button(bar, text='Refresh', command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        ttk.Label(bar, text='Obsolete, Not For New Designs and Last Time Buy. One row per BR; expand a product to see all projects and revisions.',
                  wraplength=1050).pack(side=tk.LEFT)
        self.view = table(self, [('mpn', 'MPN', 190),
            ('alternate_part_number', 'Alternate Part Number', 220),
            ('manufacturer', 'Manufacturer', 150), ('description', 'Description', 220),
            ('product_status', 'Product Status', 110), ('project', 'Top-level project', 160),
            ('subproject', 'Subproject', 140), ('board', 'Card', 150),
            ('revision', 'Revision', 80), ('scope', 'Usage', 110),
            ('refs', 'References', 120), ('qty', 'Quantity / board', 100),
            ('total', 'Quantity / project', 110)])
        self.view.configure(show='tree headings')
        self.view.heading('#0', text='BR')
        self.view.column('#0', width=180, minwidth=120, stretch=False)
        ttk.Label(self, textvariable=self.summary, padding=10).pack(fill=tk.X)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after_id = self.after(100, self.poll)
        self.refresh()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state='disabled')
        self.summary.set('Loading...')
        def worker():
            try:
                self.jobs.put((model.obsolete_products(), None))
            except Exception as exc:
                self.jobs.put((None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            records, error = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.refresh_button.configure(state='normal')
            self.view.delete(*self.view.get_children())
            self.records = records or []
            for index, part in enumerate(self.records):
                fields = ('mpn', 'alternate_part_number', 'manufacturer', 'description', 'product_status')
                values = [part[k] for k in fields]
                count = len(part['usages'])
                values.extend(('', '', '', '', f'{count} usage rows' if count else 'No linked usage', '', '', ''))
                self.view.insert('', 'end', iid=str(index), text=part['cmp'], values=values, open=False)
                for usage_index, source in enumerate(part['usages']):
                    usage = source['usage']
                    # Different catalog records sharing a BR retain their own details.
                    details = [source[k] for k in fields] if len(part['components']) > 1 else [''] * len(fields)
                    details.extend((usage['parent_project'] or usage['project'],
                        usage['project'] if usage['parent_project'] else '—', usage['board'],
                        usage['revision'], 'Current' if usage['active'] else 'Previous',
                        usage['refs'], f"{usage['quantity']:g}",
                        f"{usage['quantity'] * usage['board_quantity']:g}"))
                    self.view.insert(str(index), 'end', iid=f'{index}:{usage_index}',
                        text=f"{usage['board']} / Rev. {usage['revision']}", values=details)
            count = len(self.records)
            self.summary.set(f'Unable to load products: {error}' if error else
                f'{count} products; {sum(len(p["usages"]) for p in self.records)} usage rows. Expand a product to view its usage.'
                if count else 'No Obsolete, Not For New Designs or Last Time Buy products found.')
        self.after_id = self.after(100, self.poll)

    def close(self):
        self.after_cancel(self.after_id)
        self.destroy()
