"""Tkinter host for the KiCad LCSC Manager search and conversion core."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
import webbrowser

import lcsc_api
import lcsc_library
import lcsc_settings
from lcsc_preview import build_previews
from ui.cad_preview import CadPreview
from ui.step_preview import StepPreview


class LcscManagerWindow(tk.Toplevel):
    def __init__(self, parent, on_apply, initial_query=''):
        super().__init__(parent)
        self.title('LCSC Manager — Component and KiCad libraries')
        self.geometry('1120x760')
        self.minsize(900, 650)
        # A transient window loses the native maximize control on Windows.
        # Keep a normal window frame while retaining the existing modal workflow.
        self.resizable(True, True)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.on_apply = on_apply
        self.client = lcsc_api.LcscAPI()
        self.results = {}
        self.product = None
        self.page = 0
        self.search_query = ('', '')
        self.busy = False
        self.pending_detail = None
        self.events = queue.Queue()
        self.buttons = []
        self.query = tk.StringVar(value=initial_query)
        self.package = tk.StringVar()
        self.status = tk.StringVar(value='Search by manufacturer part number, value, or LCSC code (for example C25804).')
        settings = lcsc_settings.load_settings()
        self.destination = tk.StringVar(value=settings['library_directory'])
        self.symbol = tk.BooleanVar(value=True)
        self.footprint = tk.BooleanVar(value=True)
        self.model = tk.BooleanVar(value=True)
        self.replace = tk.BooleanVar(value=False)
        self._build()
        self.poll_id = self.after(100, self._poll)
        if initial_query:
            self.after_idle(self.search)

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.buttons.append(button)
        return button

    def _build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill='x')
        ttk.Label(top, text='Part / value / LCSC:').pack(side='left')
        entry = ttk.Entry(top, textvariable=self.query, width=36)
        entry.pack(side='left', fill='x', expand=True, padx=6)
        entry.bind('<Return>', lambda event: self.search())
        ttk.Label(top, text='Package:').pack(side='left')
        ttk.Entry(top, textvariable=self.package, width=14).pack(side='left', padx=6)
        self._button(top, 'Search', self.search).pack(side='left', padx=4)
        self._button(top, 'Load more', lambda: self.search(more=True)).pack(side='left')
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side='bottom', fill='x')
        self._button(bottom, 'Load details into form', self.apply_details).pack(side='left')
        self._button(bottom, 'Close', self.close).pack(side='right')
        ttk.Label(bottom, textvariable=self.status, wraplength=1020).pack(side='bottom', fill='x', pady=(10, 0))
        options = ttk.LabelFrame(self, text='KiCad library destination', padding=8)
        options.pack(side='bottom', fill='x', padx=10)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text='Library folder').grid(row=0, column=0, sticky='w', padx=6)
        ttk.Entry(options, textvariable=self.destination).grid(row=0, column=1, sticky='ew')
        self._button(options, 'Browse…', lambda: self.browse(self.destination)).grid(row=0, column=2, padx=5)
        self._button(options, 'Download CAD files', self.download_selected).grid(row=0, column=3, padx=5)
        checks = ttk.Frame(options)
        checks.grid(row=1, column=0, columnspan=4, sticky='w', pady=8)
        for label, variable in (('Symbol', self.symbol), ('Footprint', self.footprint), ('3D models (STEP / WRL)', self.model),
                                 ('Replace existing files', self.replace)):
            ttk.Checkbutton(checks, text=label, variable=variable).pack(side='left', padx=6)
        panes = ttk.Panedwindow(self, orient='vertical')
        panes.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        result_frame = ttk.Frame(panes)
        panes.add(result_frame, weight=2)
        columns = ('lcsc', 'mpn', 'manufacturer', 'package', 'stock', 'price', 'type')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='headings', selectmode='browse', height=8)
        for column, label, width in zip(columns,
                ('LCSC', 'Manufacturer Part Number', 'Manufacturer', 'Package', 'JLCPCB stock', 'Price (USD)', 'Type'),
                (85, 230, 200, 110, 100, 95, 85)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=65)
        scroll = ttk.Scrollbar(result_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.load_detail)
        self.tree.bind('<Double-1>', self.load_detail)
        self.preview_tabs = ttk.Notebook(panes)
        panes.add(self.preview_tabs, weight=3)
        self.cad_panes = tk.PanedWindow(self.preview_tabs, orient='horizontal', sashwidth=6,
                                       borderwidth=0, background='#d0d0d0', showhandle=False)
        self.preview_tabs.add(self.cad_panes, text='CAD previews')
        frames = [ttk.LabelFrame(self.cad_panes, text=label) for label in ('Symbol', 'Footprint', '3D Model')]
        for frame in frames:
            self.cad_panes.add(frame, minsize=260, stretch='always', width=350)
        self.symbol_preview = CadPreview(frames[0], compact=True)
        self.footprint_preview = CadPreview(frames[1], compact=True)
        self.step_preview = StepPreview(frames[2], lambda: self.product, lambda: self.destination.get())
        for preview in (self.symbol_preview, self.footprint_preview, self.step_preview):
            preview.pack(fill='both', expand=True)
        self.preview_tabs.bind('<<NotebookTabChanged>>', self._preview_tab_changed)
        details_frame = ttk.Frame(self.preview_tabs, padding=6)
        self.preview_tabs.add(details_frame, text='Specifications')
        links = ttk.Frame(details_frame)
        links.pack(side='bottom', fill='x')
        self._button(links, 'Load selected details', self.load_detail).pack(side='left')
        self._button(links, 'Datasheet', lambda: self.open_link('datasheet')).pack(side='left', padx=5)
        self._button(links, 'LCSC product page', lambda: self.open_link('url')).pack(side='left')
        self.details = tk.Text(details_frame, height=7, wrap='word', state='disabled')
        self.details.pack(fill='both', expand=True)

    def browse(self, variable):
        path = filedialog.askdirectory(parent=self, initialdir=variable.get() or None)
        if path:
            variable.set(path)

    def _run(self, operation, success, message):
        if self.busy:
            return
        self.busy = True
        self.status.set(message)
        for button in self.buttons:
            button.configure(state='disabled')

        def worker():
            try:
                self.events.put((success, operation(), None))
            except Exception as exc:
                self.events.put((success, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            success, value, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for button in self.buttons:
                button.configure(state='normal')
            if error:
                self.status.set(str(error))
                if self.product is None:
                    self._clear_previews(f'Preview unavailable: {error}')
                messagebox.showerror('LCSC Manager', str(error), parent=self)
            else:
                try:
                    success(value)
                except Exception as exc:
                    self.status.set(str(exc))
                    messagebox.showerror('LCSC Manager', str(exc), parent=self)
            pending, self.pending_detail = self.pending_detail, None
            if pending and self.tree.selection() == (pending,):
                self.after_idle(self.load_detail)
        self.poll_id = self.after(100, self._poll)

    def search(self, more=False):
        if self.busy:
            return
        query = self.search_query if more else (self.query.get().strip(), self.package.get().strip())
        if not query[0]:
            self.status.set('Enter a part number or value to search.')
            return
        page = self.page + 1 if more else 1

        def success(products):
            self.search_query, self.page = query, page
            if not more:
                self.tree.delete(*self.tree.get_children())
                self.results.clear()
                self.product = None
                self._clear_previews('Select a component to view its CAD drawing.')
                self._show_details('Select a result to load its specifications.')
            for product in products:
                code = product['lcsc_id']
                self.results[code] = product
                values = (code, product['manufacturer_part'], product['manufacturer'], product['package'],
                          lcsc_api.get_lcsc_stock(product) or '—', lcsc_api.get_lcsc_price(product) or '—', product['library_type'])
                if not self.tree.exists(code):
                    self.tree.insert('', 'end', iid=code, values=values)
            self.status.set(f'{len(self.results)} results. Select a component to view details.' if products else 'No more results found.')
            if products and not more:
                self.tree.selection_set(products[0]['lcsc_id'])

        self._run(lambda: self.client.search(query[0], page=page, package=query[1]), success, 'Searching LCSC / JLCPCB…')

    def _show_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def _clear_previews(self, message):
        self.symbol_preview.set_scenes(message=message)
        self.footprint_preview.set_scenes(message=message)
        self.step_preview.reset_component()

    def _preview_tab_changed(self, event=None):
        if self.preview_tabs.select() == str(self.cad_panes) and self.product:
            self.step_preview.load_component(force=False)

    def load_detail(self, event=None):
        selection = self.tree.selection()
        self.product = None
        self._clear_previews('Loading CAD previews…')
        if not selection:
            self._clear_previews('Select a component to view its CAD drawing.')
            return
        code = selection[0]
        if self.busy:
            self.pending_detail = code
            self._show_details('Loading the selected component after the current request finishes…')
            return

        def success(result):
            if self.tree.selection() != (code,):
                return
            product, previews = result
            self.product = product
            if not product:
                self._clear_previews('No CAD data found for this component.')
                self.status.set('No details found for the selected component.')
                return
            self.symbol_preview.set_scenes(previews['symbol'],previews.get('symbol_error','Symbol preview unavailable.'))
            self.footprint_preview.set_scenes(previews['footprint'],previews.get('footprint_error','Footprint preview unavailable.'))
            self._preview_tab_changed()
            raw = product.get('easyeda_data') or {}
            lines = [f'{code} — {product["manufacturer_part"]}', product['description'],
                     f'Manufacturer: {product["manufacturer"]}', f'Package: {product["package"]}',
                     f'EasyEDA symbol: {"Available" if raw.get("dataStr") else "Unavailable"}; '
                     f'footprint: {"Available" if raw.get("packageDetail") else "Unavailable"}',
                     '3D model availability is checked during download.', '']
            lines.extend(f'{key}: {value}' for key, value in product.get('parameters', {}).items())
            lines.extend(product.get('warnings', []))
            self._show_details('\n'.join(lines))
            self.status.set('Details loaded. Load details into the form, or download CAD files to the Library folder.')

        def operation():
            product = self.client.details(code)
            return product, build_previews(product) if product else {}

        self._run(operation, success, f'Loading {code} from EasyEDA / JLCPCB…')

    def _selected_product(self):
        if not self.product or self.tree.selection() != (self.product['lcsc_id'],):
            messagebox.showinfo('LCSC Manager', 'Select a component and load its details first.', parent=self)
            return None
        return self.product

    def apply_details(self):
        product = self._selected_product()
        if product:
            self.on_apply(product, {})
            self.status.set('Component details loaded into the record form. Use Add or Update there to save.')

    def download_selected(self):
        product = self._selected_product()
        if not product:
            return
        options = dict(symbol=self.symbol.get(), footprint=self.footprint.get(), model=self.model.get(),
                       register=False, replace=self.replace.get(), flat=True)
        if not any(options[key] for key in ('symbol', 'footprint', 'model')):
            messagebox.showinfo('LCSC Manager', 'Select Symbol, Footprint or 3D models to download.', parent=self)
            return
        destination = self.destination.get()

        def success(result):
            message = (f'CAD files are available in the selected Library folder:\n{destination}'
                       if result.get('files') else 'No CAD files were downloaded. See the details below.')
            message += '\n\n' + '\n'.join(result.get('files', []) + result.get('warnings', []))
            self.status.set(f'CAD download completed: {destination}' if result.get('files') else 'No CAD files available for download.')
            messagebox.showinfo('LCSC Manager', message, parent=self)

        self._run(lambda: lcsc_library.import_component(product, destination, **options), success,
                  'Converting symbol / footprint and downloading available 3D models…')

    def open_link(self, key):
        product = self._selected_product()
        url = product.get(key, '') if product else ''
        parsed = urlsplit(url)
        if parsed.scheme in ('https', 'http') and parsed.hostname:
            webbrowser.open(url)
        elif product:
            self.status.set('No link is available for this component.')

    def close(self):
        if self.busy:
            self.status.set('Please wait for the current request or download to finish before closing.')
            return
        self.after_cancel(self.poll_id)
        self.grab_release()
        self.destroy()
