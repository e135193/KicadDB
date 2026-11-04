from pathlib import Path
import tempfile
import tkinter as tk
import time
import unittest
from unittest.mock import Mock, patch

from lcsc_preview import symbol_library_previews, footprint_preview, render_png
from ui.library_preview import LibraryPreview
from ui.footprint_window import show_suggestions


class TestLibraryPreviews(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.library=self.root/'Example.kicad_sym'
        self.library.write_text('''(kicad_symbol_lib
          (symbol "Wrong" (symbol "Wrong_0_1" (circle (center 0 0)(radius 1))))
          (symbol "Base" (symbol "Base_0_1" (rectangle (start -1 2)(end 1 -2)))
            (symbol "Base_1_1" (pin passive line (at 0 4 270)(length 2)(name "~")(number "1"))))
          (symbol "Derived" (extends "Base")))''',encoding='utf-8')
        self.footprint=self.root/'test.kicad_mod'
        self.footprint.write_text('''(footprint test
          (fp_rect (start -3 -3)(end 3 3)(layer F.CrtYd))
          (fp_line (start -2 -2)(end 2 -2)(layer F.SilkS))
          (fp_circle (center 0 0)(end 2 0)(layer F.Fab))
          (pad "1" thru_hole circle (at 0 0)(size 2 2)(drill 1)(layers "*.Cu" "*.Mask")))''')

    def test_exact_symbol_and_inherited_geometry_without_writes(self):
        before=self.library.read_bytes()
        scene=symbol_library_previews(self.library,'Example:Derived')[0]
        self.assertIn('1 pins',scene.summary)
        self.assertIn('>1</text>',scene.body)
        self.assertNotIn('<circle',scene.body)
        self.assertEqual(self.library.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'not found'):
            symbol_library_previews(self.library,'Example:Missing')

    def test_footprint_reference_palette_and_dark_background(self):
        scene=footprint_preview(self.footprint.read_text())
        self.assertEqual(scene.background,'#001023')
        for color in ('#c83434','#f2eda1','#afafaf','#ff26e2','#b2b7bd','#e3b72e'):
            self.assertIn(color,scene.body)
        self.assertTrue(scene.grid)
        self.assertTrue(render_png(scene,300,300).startswith(b'\x89PNG'))

    def test_suggestion_window_selection_loads_preview_on_right(self):
        root=tk.Tk()
        root.withdraw()
        result={'messages':[], 'candidates':[dict(name='Test:Example',path=str(self.footprint),level='Library',reason='Test',source='Local',evidence='Test')]}
        window=show_suggestions(root,'MPN',result,lambda _:True,lambda _:None)
        window.withdraw()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        preview=next(w for w in descendants(window) if isinstance(w,LibraryPreview))
        try:
            with patch.object(preview.canvas,'winfo_width',return_value=400),patch.object(preview.canvas,'winfo_height',return_value=300):
                deadline=time.monotonic()+6
                while preview.photo is None and time.monotonic()<deadline:
                    root.update()
                    time.sleep(.01)
                self.assertIsNotNone(preview.photo)
                self.assertIn('1 pads',preview.scene.summary)
                preview.show_file(None)
                self.assertIsNone(preview.scene)
        finally:
            window.destroy()
            root.destroy()

    def test_symbol_suggestion_updates_right_preview_and_applies_exact_selection(self):
        from Brensan_Kicad_DB import App
        root=tk.Tk()
        root.withdraw()
        app=App.__new__(App)
        app.root=root
        app.component_type_combo=Mock()
        app.component_type_combo.get.return_value='Resistor'
        app.current_table='Resistor'
        app.status_var=tk.StringVar(master=root)
        field=tk.Entry(root)
        app.entry_widgets={'Symbols':field}
        window=None
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            with patch('Brensan_Kicad_DB.get_kicad_symbol_file_for_component_type',return_value=str(self.library)):
                app.open_symbol_suggestion_window(['Example:Wrong','Example:Derived'])
            window=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
            # Listbox-generated events require mapped widgets on Windows.
            root.attributes('-alpha', 0)
            window.attributes('-alpha', 0)
            root.deiconify()
            window.deiconify()
            widgets=list(descendants(window))
            preview=next(w for w in widgets if isinstance(w,LibraryPreview))
            listing=next(w for w in widgets if isinstance(w,tk.Listbox))
            root.update()
            listing.selection_clear(0,'end')
            listing.selection_set(1)
            listing.event_generate('<<ListboxSelect>>', when='tail')
            deadline=time.monotonic()+6
            while (preview.scene is None or '1 pins' not in preview.scene.summary) and time.monotonic()<deadline:
                root.update()
                time.sleep(.01)
            self.assertIsNotNone(preview.scene)
            self.assertIn('1 pins',preview.scene.summary)
            button=next(w for w in widgets if w.winfo_class()=='TButton' and w.cget('text')=='Select')
            button.invoke()
            self.assertEqual(field.get(),'Example:Derived')
        finally:
            root.destroy()

    def test_record_form_passes_saved_footprint_to_suggestions(self):
        from Brensan_Kicad_DB import App
        root = tk.Tk()
        root.withdraw()
        app = App.__new__(App)
        app.root = root
        app.current_table = 'Mosfet'
        app.selected_key_column, app.selected_key_value = 'ID', 1
        app.entry_widgets = {}
        for name, value in {'Manufacturer_Part_Number':'BSC019N06NSATMA1',
                            'Footprints':'BrensanDB:PG-TDSON-8-FL',
                            'Description':'MOSFET TDSON-8 FL', 'Package':'8-PowerTDFN'}.items():
            field = tk.Entry(root)
            field.insert(0, value)
            app.entry_widgets[name] = field
        app.run_in_background = lambda worker, *args, **kwargs: worker(lambda _:None, lambda:False)
        try:
            with patch('footprint_suggestions.suggest') as suggest:
                app.on_footprints_label_clicked()
            self.assertEqual(suggest.call_args.kwargs['current_footprint'], 'BrensanDB:PG-TDSON-8-FL')
            self.assertEqual(suggest.call_args.kwargs['description'], 'MOSFET TDSON-8 FL')
        finally:
            root.destroy()
