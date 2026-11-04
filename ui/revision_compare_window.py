"""Read-only comparison of two saved revisions of one board."""
import tkinter as tk
from tkinter import ttk, messagebox

import product_tree as model
from ui.product_tree_window import table


class RevisionCompareWindow(tk.Toplevel):
    def __init__(self, parent, board_id, board_name, revision_id=None):
        super().__init__(parent)
        self.title("Compare revisions — " + board_name)
        self.geometry("1150x640")
        self.minsize(800, 450)
        self.transient(parent)
        self.app = parent
        self.records = []
        revisions = model.board_revisions(board_id)
        self.revisions = {f"{r['revision']} · {r['status']} · {r['imported_at']}": r for r in revisions}
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=board_name).pack(anchor=tk.W, pady=(0, 8))
        bar = ttk.Frame(body)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="From:").pack(side=tk.LEFT)
        self.before = ttk.Combobox(bar, values=list(self.revisions), state="readonly", width=35)
        self.before.pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text="To:").pack(side=tk.LEFT)
        self.after_revision = ttk.Combobox(bar, values=list(self.revisions), state="readonly", width=35)
        self.after_revision.pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Compare", command=self.compare).pack(side=tk.LEFT)
        self.summary = tk.StringVar(value="Select two revisions of the same board.")
        ttk.Label(body, textvariable=self.summary, padding=(0, 10)).pack(fill=tk.X)
        self.results = table(body, [("change", "Change", 80), ("part", "Component", 180),
            ("refs", "References", 110), ("field", "Field", 130), ("before", "Before", 250), ("after", "After", 250)])
        self.detail = tk.Text(body, height=5, wrap=tk.WORD, state="disabled")
        self.detail.pack(fill=tk.X, pady=8)
        self.results.bind("<<TreeviewSelect>>", self.show_detail)
        ttk.Button(body, text="Export comparison to Excel", command=lambda: self.app.export(self.records)).pack(anchor=tk.E)
        labels = list(self.revisions)
        if len(labels) >= 2:
            target = next((label for label, r in self.revisions.items() if r["id"] == revision_id), labels[0])
            self.after_revision.set(target)
            self.before.set(next(label for label in labels if label != target))
            self.compare()

    def compare(self):
        old, new = self.revisions.get(self.before.get()), self.revisions.get(self.after_revision.get())
        if not old or not new:
            return
        if old["id"] == new["id"]:
            messagebox.showinfo("Compare revisions", "Select two different revisions.", parent=self)
            return
        def done(result):
            if self.winfo_exists():
                self.render(result)
        self.app.run(lambda: model.compare_revisions(old["id"], new["id"]), done)

    def render(self, result):
        self.records = []
        self.results.delete(*self.results.get_children())
        self.detail.configure(state="normal")
        self.detail.delete("1.0", tk.END)
        self.detail.configure(state="disabled")
        def summary(line):
            if not line:
                return ""
            return (f"CMP: {line['cmp']} | MPN: {line['mpn']} | Manufacturer: {line['manufacturer']} | "
                    f"Qty: {line['quantity']:g} | References: {line['references']} | Description: {line['description']}")
        counts = {kind: sum(c["kind"] == kind for c in result["changes"]) for kind in ("Added", "Removed", "Changed")}
        self.summary.set(f"{result['before']['revision']} → {result['after']['revision']} | " +
                         " · ".join(f"{key}: {value}" for key, value in counts.items()) +
                         f" · Revision metadata changes: {len(result['metadata'])}")
        locations = {r["id"]: f"{r['table_name'] or 'Deleted component'} / {r['row_key'] or r['id']}"
                     for r in model.rows("SELECT * FROM PT_Components")}
        for change in result["changes"]:
            line = change["after"] or change["before"]
            fields = change["fields"] or [("BOM row", summary(change["before"]), summary(change["after"]))]
            for field, old, new in fields:
                if field == "Database match":
                    old, new = locations.get(old, "Unmatched"), locations.get(new, "Unmatched")
                self.records.append({"Change": change["kind"], "Component": line["cmp"] or line["mpn"],
                    "References": line["references"], "Field": field, "Before": old if old is not None else "",
                    "After": new if new is not None else ""})
        for field, old, new in result["metadata"]:
            self.records.append({"Change": "Revision", "Component": "", "References": "", "Field": field, "Before": old, "After": new})
        for index, record in enumerate(self.records):
            self.results.insert("", "end", iid=str(index), values=list(record.values()))

    def show_detail(self, event=None):
        selection = self.results.selection()
        if not selection:
            return
        record = self.records[int(selection[0])]
        self.detail.configure(state="normal")
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", "\n".join(f"{key}: {value}" for key, value in record.items()))
        self.detail.configure(state="disabled")
