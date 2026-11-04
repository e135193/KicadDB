"""Project summary, latest-revision materials and review suggestions."""
import tkinter as tk
from tkinter import ttk


class ProjectOverviewPanel(ttk.Frame):
    FILTERS = ('All materials', 'Obsolete', 'Lifecycle risk', 'Critical (IC / MOSFET)', 'Stock shortages', 'Unmatched')

    def __init__(self, parent, make_table, open_card):
        super().__init__(parent)
        self.report = None
        self.open_card = open_card
        self.summary = tk.StringVar()
        summary_label = ttk.Label(self, textvariable=self.summary, padding=(6, 4), wraplength=600)
        summary_label.pack(fill=tk.X)
        metrics = ttk.Frame(self)
        metrics.pack(fill=tk.X, pady=4)
        self.metrics = {}
        for index, (key, label) in enumerate(zip(
                ('materials', 'obsolete', 'lifecycle_risk', 'critical', 'shortages', 'unmatched_parts'), self.FILTERS)):
            value = self.metrics[key] = tk.StringVar(value=f'{label}: —')
            ttk.Button(metrics, textvariable=value, command=lambda name=label: self.filter_materials(name)).grid(
                row=index // 3, column=index % 3, sticky='ew', padx=2, pady=2)
        for column in range(3):
            metrics.columnconfigure(column, weight=1, uniform='metric')
        self.scope = ttk.Label(self, text='Latest linked revision per card in this project and its descendants: '
            'final revisions (-A, -B, ...) take priority over prototypes (-X1, -X2, ...). '
            'Critical = ICs (including MCUs) and MOSFETs. Stock comparison is for one project, without reservations.',
            wraplength=600, padding=(6, 4))
        self.scope.pack(fill=tk.X)
        self.bind('<Configure>', lambda event: (
            self.scope.configure(wraplength=max(250, event.width - 16)),
            summary_label.configure(wraplength=max(250, event.width - 16))))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=tk.BOTH, expand=True, pady=4)
        materials_page, cards_page, advice_page = (ttk.Frame(self.tabs, padding=4) for _ in range(3))
        for page, name in ((materials_page, 'Project materials'), (cards_page, 'Cards / BOMs'), (advice_page, 'Recommendations')):
            self.tabs.add(page, text=name)
        self.materials_page = materials_page
        bar = ttk.Frame(materials_page)
        bar.pack(fill=tk.X, pady=(0, 5))
        self.filter = tk.StringVar(value=self.FILTERS[0])
        selector = ttk.Combobox(bar, textvariable=self.filter, values=self.FILTERS, state='readonly', width=27)
        selector.pack(side=tk.LEFT)
        selector.bind('<<ComboboxSelected>>', lambda event: self.filter_materials())
        self.count = tk.StringVar()
        ttk.Label(bar, textvariable=self.count, padding=5).pack(side=tk.LEFT)
        self.materials = make_table(materials_page, [
            ('mpn', 'MPN', 190), ('category', 'Category', 110),
            ('product_status', 'Product Status', 140), ('quantity', 'Quantity / project', 120),
            ('stock', 'Company Stock', 110), ('shortage', 'Shortage', 90),
            ('alternate', 'Alternate Part Number', 190), ('cards', 'Card placements', 105),
            ('match', 'Match', 140), ('description', 'Description', 240), ('uses', 'Used on / revision', 300)])
        self.materials.tag_configure('risk', background='#ffe0e0')
        self.materials.tag_configure('shortage', background='#fff2cf')
        self.materials.tag_configure('unmatched', background='#f0f0f0')
        ttk.Label(cards_page, text='Double-click a card to open its latest linked BOM.', padding=4).pack(anchor=tk.W)
        self.cards = make_table(cards_page, [('project', 'Project / subproject', 150),
            ('card', 'Card part number', 160), ('name', 'Card name', 230), ('revision', 'Revision', 90),
            ('status', 'BOM status', 100), ('quantity', 'Boards / project', 120), ('rows', 'BOM rows', 90),
            ('responsible', 'Responsible', 160)])
        self.cards.tag_configure('empty', background='#ffe0e0')
        self.cards.bind('<Double-1>', self.open_selected_card)
        self.advice = make_table(advice_page, [('number', '#', 65), ('review', 'Recommended review', 780)])

    def clear(self, message):
        self.report = None
        self.summary.set(message)
        self.count.set('')
        for key, name in zip(self.metrics, self.FILTERS):
            self.metrics[key].set(f'{name}: —')
        for view in (self.materials, self.cards, self.advice):
            view.delete(*view.get_children())

    def render(self, report):
        self.report = report
        summary = report['summary']
        self.summary.set(f"{summary['subprojects']} subprojects · {summary['cards']} cards / {summary['card_links']} placements · "
            f"{summary['latest_boms']} latest BOMs ({summary['approved_boms']} Approved, "
            f"{summary['draft_boms']} Draft, {summary['empty_boms']} empty)")
        for key, name in zip(self.metrics, self.FILTERS):
            self.metrics[key].set(f'{name}: {summary[key]}')
        self.filter_materials()
        self.cards.delete(*self.cards.get_children())
        for index, card in enumerate(report['cards']):
            self.cards.insert('', 'end', iid=str(index), values=(card['project_code'], card['part_number'],
                card['board_name'], card['revision'], card['status'], f"{card['board_quantity']:g}",
                card['line_count'], card['responsible']), tags=('empty',) if not card['line_count'] else ())
        self.advice.delete(*self.advice.get_children())
        for index, suggestion in enumerate(report['suggestions'], 1):
            self.advice.insert('', 'end', values=(index, suggestion))

    def filter_materials(self, name=None):
        if name is not None:
            self.filter.set(name)
            self.tabs.select(self.materials_page)
        self.materials.delete(*self.materials.get_children())
        if self.report is None:
            return
        def number(value):
            return '—' if value is None else f'{value:g}'
        for index, part in enumerate(self.report['materials']):
            matches = dict(zip(self.FILTERS, (True, part['obsolete'], part['lifecycle_risk'], part['critical'],
                part['shortage'] is not None and part['shortage'] > 0, part['unmatched'])))
            if not matches[self.filter.get()]:
                continue
            tag = ('risk',) if part['lifecycle_risk'] else ('shortage',) if matches['Stock shortages'] else ('unmatched',) if part['unmatched'] else ()
            self.materials.insert('', 'end', iid=str(index), values=(part['cmp'], part['mpn'], part['category'],
                part['product_status'] or 'Unknown', number(part['quantity']), number(part['stock']), number(part['shortage']),
                part['alternate_part_number'], part['card_count'], 'Needs saved match' if part['unmatched'] else 'Matched',
                part['description'], part['uses']), tags=tag)
        count = len(self.materials.get_children())
        self.count.set(f'{count} / {len(self.report["materials"])} materials' if count else 'No materials in this filter.')

    def open_selected_card(self, event=None):
        selected = self.cards.selection()
        if len(selected) == 1 and self.report is not None:
            card = self.report['cards'][int(selected[0])]
            self.open_card(card['project_id'], card['revision_id'])
