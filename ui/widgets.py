import tkinter as tk
from tkinter import ttk

# Small helpers for UI widgets; more to implement as needed

def create_verified_combobox(parent, var, width=20):
    cb = ttk.Combobox(parent, values=["Verified", "NOT Verified"], textvariable=var, width=width, state='readonly')
    return cb


def create_symbol_picker(parent, values, width=40):
    cb = ttk.Combobox(parent, values=values, width=width)
    return cb


def create_footprint_picker(parent, values, width=40):
    cb = ttk.Combobox(parent, values=values, width=width)
    return cb


def build_main_ui(app):
    """Constructs the main window widgets and attaches them onto the provided `app` instance."""
    main_frame = ttk.Frame(app.root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    toolbar = ttk.Frame(main_frame)
    toolbar.pack(fill=tk.X, pady=(0, 10))
    viewport = tk.Canvas(toolbar, highlightthickness=0, borderwidth=0)
    viewport.pack(fill=tk.X)
    top_frame = ttk.Frame(viewport)
    toolbar_window = viewport.create_window(0, 0, window=top_frame, anchor='nw')
    toolbar_scroll = ttk.Scrollbar(toolbar, orient=tk.HORIZONTAL, command=viewport.xview)
    viewport.configure(xscrollcommand=toolbar_scroll.set)

    def resize_toolbar(event=None):
        width, height = top_frame.winfo_reqwidth(), top_frame.winfo_reqheight()
        viewport.itemconfigure(toolbar_window, width=max(width, viewport.winfo_width()))
        viewport.configure(height=height, scrollregion=(0, 0, width, height))
        if width > viewport.winfo_width():
            toolbar_scroll.pack(fill=tk.X)
        else:
            toolbar_scroll.pack_forget()
            viewport.xview_moveto(0)

    top_frame.bind('<Configure>', resize_toolbar)
    viewport.bind('<Configure>', resize_toolbar)

    ttk.Label(top_frame, text='Table:').pack(side=tk.LEFT)
    app.table_combo = ttk.Combobox(top_frame, state="readonly", width=35)
    app.table_combo.pack(side=tk.LEFT, padx=5)
    app.table_combo.bind("<<ComboboxSelected>>", app.on_table_selected)

    ttk.Button(top_frame, text='Refresh tables', command=app.load_tables).pack(side=tk.LEFT, padx=5)
    ttk.Label(top_frame, text='Search:').pack(side=tk.LEFT, padx=(20, 5))
    app.search_var = tk.StringVar()
    app.search_entry = ttk.Entry(top_frame, textvariable=app.search_var, width=35)
    app.search_entry.pack(side=tk.LEFT)
    app.search_entry.bind("<Return>", lambda event: app.search_records())

    # Kept on `app` so they can be disabled while a background job is running.
    app.busy_buttons = []
    for text, command in [
        ('Search', app.search_records),
        ("DigiKey", app.digikey_search_records),
        ("LCSC", app.lcsc_search_records),
        ("Check BOM List", app.select_bom_file),
        ("Product Tree", app.open_product_tree),
        ("Obsolete Products", app.open_obsolete_products),
    ]:
        button = ttk.Button(top_frame, text=text, command=command)
        button.pack(side=tk.LEFT, padx=5)
        app.busy_buttons.append(button)
    ttk.Label(top_frame, textvariable=app.bom_file_label_var).pack(side=tk.LEFT, padx=5)
    ttk.Button(top_frame, text='Settings', command=lambda: getattr(app, 'open_settings_window', lambda: None)()).pack(side=tk.RIGHT, padx=5)

    content_frame = ttk.Frame(main_frame)
    content_frame.pack(fill=tk.BOTH, expand=True)

    app.form_outer_frame = ttk.LabelFrame(content_frame, text='Record form', padding=10)
    app.form_outer_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

    form_content_frame = ttk.Frame(app.form_outer_frame)
    form_content_frame.pack(fill=tk.BOTH, expand=True)

    app.form_canvas = tk.Canvas(form_content_frame, width=360)
    app.form_scrollbar = ttk.Scrollbar(form_content_frame, orient=tk.VERTICAL, command=app.form_canvas.yview)
    app.form_frame = ttk.Frame(app.form_canvas)
    app.form_frame.bind("<Configure>", lambda event: app.form_canvas.configure(scrollregion=app.form_canvas.bbox("all")))
    app.form_canvas.create_window((0, 0), window=app.form_frame, anchor="nw")
    app.form_canvas.configure(yscrollcommand=app.form_scrollbar.set)
    app.form_canvas.pack(side=tk.LEFT, fill=tk.Y, expand=True)
    app.form_scrollbar.pack(side=tk.LEFT, fill=tk.Y)

    right_form_panel = ttk.Frame(form_content_frame)
    right_form_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
    ttk.Button(right_form_panel, text='New / Clear form', command=app.clear_form).pack(fill=tk.X, pady=2)
    app.save_record_button = ttk.Button(right_form_panel, text='Save', command=app.add_record)
    app.save_record_button.pack(fill=tk.X, pady=2)
    app.update_record_button = ttk.Button(right_form_panel, text='Update', command=app.update_record)
    app.update_record_button.pack(fill=tk.X, pady=2)
    app.delete_record_button = ttk.Button(right_form_panel, text='Delete', command=app.delete_record)
    app.delete_record_button.pack(fill=tk.X, pady=2)

    info_frame = ttk.Frame(right_form_panel)
    info_frame.pack(fill=tk.X, pady=(30, 0))
    ttk.Label(info_frame, text="Company Stock:").pack(anchor=tk.W)
    ttk.Label(info_frame, textvariable=app.company_stock_var).pack(anchor=tk.W, pady=(0, 12))
    ttk.Label(info_frame, text="Stock QTY:").pack(anchor=tk.W)
    ttk.Label(info_frame, textvariable=app.stock_qty_var).pack(anchor=tk.W, pady=(0, 12))
    ttk.Label(info_frame, text="Unit Price:").pack(anchor=tk.W)
    ttk.Label(info_frame, textvariable=app.unit_price_var).pack(anchor=tk.W, pady=(0, 12))
    # product_status_var was populated by both supplier searches but never shown.
    ttk.Label(info_frame, text="Product Status:").pack(anchor=tk.W)
    ttk.Label(info_frame, textvariable=app.product_status_var, wraplength=150).pack(anchor=tk.W, pady=(0, 12))
    ttk.Label(right_form_panel, text="Comment").pack(anchor=tk.W, pady=(8, 2))
    app.comment_text = tk.Text(right_form_panel, width=18, height=10, wrap=tk.WORD)
    app.comment_text.pack(fill=tk.BOTH, expand=False)

    ttk.Label(right_form_panel, text="Alternate Part Number").pack(anchor=tk.W, pady=(8, 2))
    app.alternate_part_number_text = tk.Text(right_form_panel, width=18, height=10, wrap=tk.WORD)
    app.alternate_part_number_text.pack(fill=tk.BOTH, expand=False)

    table_frame = ttk.LabelFrame(content_frame, text='Records', padding=10)
    table_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)

    app.tree = ttk.Treeview(table_frame, show="headings")
    app.tree.grid(row=0, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=app.tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=app.tree.xview)
    x_scroll.grid(row=1, column=0, sticky="ew")
    app.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    app.tree.bind("<<TreeviewSelect>>", app.on_row_selected)
    app.tree.bind("<ButtonRelease-1>", app.on_tree_button_release)

    app.status_var = tk.StringVar(value='Ready')

    bottom_frame = ttk.Frame(main_frame)
    bottom_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))

    bottom_frame.columnconfigure(0, weight=1)
    bottom_frame.columnconfigure(1, weight=0)
    bottom_frame.columnconfigure(2, weight=0)
    bottom_frame.columnconfigure(3, weight=0)
    bottom_frame.columnconfigure(4, weight=0)

    ttk.Label(
        bottom_frame,
        textvariable=app.status_var,
        anchor=tk.W
    ).grid(row=0, column=0, sticky="ew")

    # Progress + cancel for long supplier/BOM jobs, which now run off the UI
    # thread. Both stay hidden until a job actually starts.
    app.progress_bar = ttk.Progressbar(bottom_frame, orient=tk.HORIZONTAL, length=180, mode="determinate")
    app.cancel_button = ttk.Button(bottom_frame, text='Cancel', command=app.cancel_background_task)
    app.progress_bar.grid(row=0, column=1, sticky="e", padx=(10, 5))
    app.cancel_button.grid(row=0, column=2, sticky="e", padx=(0, 5))
    app.progress_bar.grid_remove()
    app.cancel_button.grid_remove()

    ttk.Label(
        bottom_frame,
        textvariable=app.total_material_count_var,
        anchor=tk.E
    ).grid(row=0, column=3, sticky="e", padx=(20, 5))

    # Signed-in user; every change is recorded under this name.
    ttk.Label(
        bottom_frame,
        textvariable=app.user_label_var,
        anchor=tk.E
    ).grid(row=0, column=4, sticky="e", padx=(15, 5))
