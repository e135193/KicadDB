"""Inspectable footprint candidates; only apply the user's selected result."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

from ui.product_tree_window import table
from ui.library_preview import LibraryPreview


def show_suggestions(parent, mpn, result, apply, retry):
    window = tk.Toplevel(parent)
    window.title(f"Footprint suggestions — {mpn}")
    window.geometry("1280x640")
    window.minsize(900, 480)
    window.transient(parent)
    body = ttk.Frame(window, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(body, text=f"MPN: {mpn or '—'}", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
    ttk.Label(body, text="\n".join(result["messages"]), wraplength=950).pack(fill=tk.X, pady=8)
    split = ttk.Panedwindow(body, orient='horizontal')
    split.pack(fill=tk.BOTH, expand=True)
    list_frame = ttk.Frame(split, width=680)
    split.add(list_frame, weight=3)
    preview_frame = ttk.LabelFrame(split, text='Footprint preview', width=480)
    split.add(preview_frame, weight=2)
    preview = LibraryPreview(preview_frame)
    preview.pack(fill=tk.BOTH, expand=True)
    preview.set_scenes(message='No matching footprint file. Select the library folder to locate the footprint.')
    view = table(list_frame, [("name", 'Installed KiCad footprint', 300), ("level", 'Evidence', 110), ("why", 'Match reason', 260)])
    for i, candidate in enumerate(result["candidates"]):
        view.insert("", "end", iid=str(i), values=(candidate["name"], candidate["level"], candidate["reason"]))
    detail = tk.StringVar(value='Verify package dimensions, pin numbers and exposed pad against the datasheet.')
    ttk.Label(body, textvariable=detail, wraplength=950, padding=8).pack(fill=tk.X)
    def selected():
        selection = view.selection()
        return result["candidates"][int(selection[0])] if selection else None
    def update(event=None):
        candidate = selected()
        if candidate:
            preview.show_file(candidate.get('path'))
            detail.set(candidate["evidence"][:650] + '\nSource: ' + candidate["source"] +
                       '\nPackage/pin matches are suggestions; verify pad numbers and dimensions against the datasheet.')
        else:
            preview.show_file(None)
        apply_button.configure(state='normal' if candidate else 'disabled')
        source_button.configure(state='normal' if candidate and candidate['source'].startswith(('https://', 'http://')) else 'disabled')
    def use():
        candidate = selected()
        if candidate and apply(candidate["name"]):
            window.destroy()
    def source():
        candidate = selected()
        if candidate and candidate["source"].startswith(("https://", "http://")):
            webbrowser.open(candidate["source"])
    def folder():
        path = filedialog.askdirectory(parent=window, title='KiCad footprints or .pretty folder')
        if path:
            window.destroy()
            retry(path)
    view.bind("<<TreeviewSelect>>", update)
    bar = ttk.Frame(body)
    bar.pack(fill=tk.X, pady=8)
    ttk.Button(bar, text='Select library folder', command=folder).pack(side=tk.LEFT)
    source_button = ttk.Button(bar, text='Open source', command=source, state='disabled')
    source_button.pack(side=tk.LEFT, padx=8)
    apply_button = ttk.Button(bar, text='Apply selected footprint', command=use, state='disabled')
    apply_button.pack(side=tk.RIGHT)
    if result["candidates"]:
        view.selection_set("0")
        update()
    return window
