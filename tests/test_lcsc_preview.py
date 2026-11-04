import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from lcsc_preview import build_previews, footprint_preview, parse_sexpr, render_png, symbol_previews
from ui.cad_preview import CadPreview


class TestCADPreviews(unittest.TestCase):
    def test_lcsc_window_module_imports_when_renderer_is_missing(self):
        result = subprocess.run(
            [sys.executable, '-c', "import sys; sys.modules['resvg_py'] = None; "
             "from ui.lcsc_manager_window import LcscManagerWindow; "
             "from lcsc_library import import_component; print('LCSC available')"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LCSC available', result.stdout)

    def test_missing_renderer_has_actionable_error_and_recovers_on_retry(self):
        scene = footprint_preview('(module test (pad "1" smd rect (at 0 0)(size 1 2)))')
        with patch.dict(sys.modules, {'resvg_py': None}):
            with self.assertRaisesRegex(RuntimeError, 'Component search and library import are still available'):
                render_png(scene, 200, 120)
        self.assertTrue(render_png(scene, 200, 120).startswith(b'\x89PNG'))

    def product(self):
        path = Path(__file__).parent / 'fixtures/lcsc_manager/C25804_easyeda.json'
        raw = json.loads(path.read_text(encoding='utf-8'))['result']
        return {'lcsc_id':'C25804','name':'0603WAF1002T5E','package':'R0603','easyeda_data':raw}

    def test_real_cad_produces_symbol_and_footprint_without_supplier_calls(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected request')):
            previews = build_previews(self.product())
            self.assertIn('2 pins', previews['symbol'][0].summary)
            self.assertIn('2 pads', previews['footprint'][0].summary)
            for name in ('symbol','footprint'):
                png = render_png(previews[name][0],400,240)
                self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
                self.assertEqual(int.from_bytes(png[16:20],'big'),400)
                self.assertEqual(int.from_bytes(png[20:24],'big'),240)
                self.assertIn('>1</text>',previews[name][0].body)
                self.assertIn('>2</text>',previews[name][0].body)

    def test_cad_failures_are_independent_and_keep_metadata_available(self):
        product = self.product()
        product['easyeda_data'].pop('packageDetail')
        previews = build_previews(product)
        self.assertTrue(previews['symbol'])
        self.assertFalse(previews['footprint'])
        self.assertIn('unavailable',previews['footprint_error'])
        missing = build_previews({'lcsc_id':'C1'})
        self.assertFalse(missing['symbol'])
        self.assertIn('No EasyEDA CAD data',missing['symbol_error'])

    def test_multi_unit_symbols_are_separate_with_shared_geometry(self):
        scenes = symbol_previews('''(kicad_symbol_lib (symbol "IC"
            (symbol "IC_0_1" (rectangle (start -2 2)(end 2 -2)))
            (symbol "IC_1_1" (pin input line (at -4 0 0)(length 2)(name "A&B")(number "1")))
            (symbol "IC_2_1" (pin output line (at 4 0 180)(length 2)(name "OUT")(number "2")))
            (symbol "IC_2_2" (pin input line (at 9 9 0)(length 2)(number "99")))
        ))''')
        self.assertEqual(len(scenes),2)
        self.assertIn('A&amp;B',scenes[0].body)
        self.assertLess(scenes[0].body.index('<polygon'),scenes[0].body.index('A&amp;B'))
        self.assertNotIn('>2</text>',scenes[0].body)
        self.assertIn('>2</text>',scenes[1].body)
        self.assertNotIn('>99</text>',scenes[1].body)
        self.assertTrue(all('polygon' in p.body for p in scenes))

    def test_rotated_through_hole_and_custom_pad_geometry(self):
        scene = footprint_preview('''(footprint "connector"
          (fp_arc (start 0 0)(mid 1 1)(end 2 0)(layer F.SilkS)(stroke (width .1)))
          (pad "1" thru_hole oval (at 0 0 90)(size 2 4)(drill oval .8 1.6))
          (pad "2" smd custom (at 5 0 90)(size 1 1)
            (primitives (gr_poly (pts (xy 0 0)(xy 2 0)(xy 1 2))(width 0)(fill yes))))
        )''')
        self.assertIn('2 pads',scene.summary)
        self.assertIn('fill="#001023"',scene.body)
        self.assertTrue(render_png(scene,500,300).startswith(b'\x89PNG'))

    def test_invalid_data_and_unsupported_shapes_are_not_fake_previews(self):
        with self.assertRaises(ValueError):
            parse_sexpr('(symbol (bad)')
        with self.assertRaises(ValueError):
            footprint_preview('(module test (pad "1" smd unknown (at 0 0)(size 1 2)))')
        with self.assertRaises(ValueError):
            footprint_preview('(module test (pad "1" smd rect (at nan 0)(size 1 2)))')


class TestCADPreviewWidget(unittest.TestCase):
    def test_render_zoom_fit_clear_and_stale_result(self):
        root=tk.Tk()
        root.withdraw()
        panel=CadPreview(root)
        panel.pack()
        scene=footprint_preview('(module x (pad "1" smd rect (at 0 0)(size 1 2)))')
        try:
            with patch.object(panel.canvas,'winfo_width',return_value=400), patch.object(panel.canvas,'winfo_height',return_value=240):
                panel.set_scenes([scene])
                deadline=time.monotonic()+5
                while panel.photo is None and time.monotonic()<deadline:
                    root.update()
                    time.sleep(.01)
                self.assertIsNotNone(panel.photo)
                panel.change_zoom(2)
                self.assertEqual(panel.zoom,2)
                panel.pan=(20,30)
                panel.fit()
                self.assertEqual((panel.zoom,panel.pan),(1,(0,0)))
                old=panel.version
                panel.set_scenes(message='No drawing')
                panel.events.put((old,render_png(scene,400,240),None))
                root.update()
                time.sleep(.1)
                root.update()
                self.assertIsNone(panel.photo)
                self.assertIsNone(panel.scene)
        finally:
            panel.destroy()
            root.destroy()


if __name__=='__main__':
    unittest.main()
