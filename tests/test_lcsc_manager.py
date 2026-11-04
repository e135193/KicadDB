import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import lcsc_api
import lcsc_library
import lcsc_settings
from api_keys import ApiKeyManager
from Company_Kicad_DB import App, map_lcsc_value_to_form_column
from third_party.lcsc_manager.library import symbol_lib

FIXTURES = Path(__file__).parent / 'fixtures' / 'lcsc_manager'


class LcscFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.catalog = json.loads((FIXTURES / 'C25804_jlcpcb.json').read_text(encoding='utf-8'))
        self.cad = json.loads((FIXTURES / 'C25804_easyeda.json').read_text(encoding='utf-8'))
        self.manager = ApiKeyManager(str(self.directory / 'usage.json'), min_keys=0)
        usage = patch.object(lcsc_api, 'get_usage_manager', return_value=self.manager)
        usage.start()
        self.addCleanup(usage.stop)
        settings = patch.object(lcsc_api, 'load_settings', return_value=dict(lcsc_settings.DEFAULTS))
        settings.start()
        self.addCleanup(settings.stop)

        def response(method, url, *args, **kwargs):
            return copy.deepcopy(self.cad if 'easyeda.com/api/' in url else self.catalog)

        network = patch.object(lcsc_api.LCSCAPIClient, '_make_request', side_effect=response)
        self.request = network.start()
        self.addCleanup(network.stop)
        self.client = lcsc_api.LcscAPI()

    def product(self):
        return self.client.details('C25804')


class TestLCSCClient(LcscFixture):
    def test_search_and_details_extract_real_parameters_and_distinct_identifiers(self):
        result = self.client.search('0603WAF1002T5E')[0]
        self.assertEqual(result['lcsc_id'], 'C25804')
        self.assertEqual(result['manufacturer_part'], '0603WAF1002T5E')
        product = self.product()
        fields = lcsc_api.extract_lcsc_fields(product)
        self.assertEqual(map_lcsc_value_to_form_column('Manufacturer_Part_Number', fields), '0603WAF1002T5E')
        self.assertEqual(map_lcsc_value_to_form_column('Value', fields, 'Resistor'), '10kΩ')
        self.assertEqual(map_lcsc_value_to_form_column('Power', fields), '100mW')
        self.assertEqual(map_lcsc_value_to_form_column('Voltage', fields), '75V')
        self.assertEqual(map_lcsc_value_to_form_column('Tolerance', fields), '±1%')
        self.assertEqual(fields['Manufacturer'], 'UNI-ROYAL(Uniroyal Elec)')
        self.assertEqual(lcsc_api.get_lcsc_price(product), '0.0024')
        self.assertEqual(lcsc_api.get_lcsc_part_status(product), '')

    def test_zero_stock_and_price_are_not_missing_or_lifecycle_status(self):
        raw = self.catalog['data']['componentPageInfo']['list'][0]
        raw['stockCount'] = 0
        raw['componentPrices'] = [{'startNumber': 1, 'productPrice': 0}]
        product = self.product()
        self.assertEqual(lcsc_api.get_lcsc_stock(product), '0')
        self.assertEqual(lcsc_api.get_lcsc_price(product), '0')
        self.assertEqual(lcsc_api.get_lcsc_part_status(product), '')

    def test_catalog_part_without_cad_can_still_fill_form(self):
        self.cad = {'success': False}
        product = self.product()
        self.assertEqual(product['manufacturer_part'], '0603WAF1002T5E')
        self.assertFalse(product.get('easyeda_data'))
        with self.assertRaisesRegex(ValueError, 'no EasyEDA CAD'):
            lcsc_library.import_component(product, str(self.directory), register=False)

    def test_wrong_easyeda_identity_rejected(self):
        self.cad['result']['lcsc']['number'] = 'C9999'
        with self.assertRaisesRegex(lcsc_api.LCSCAPIError, 'different LCSC'):
            self.product()

    def test_api_errors_are_not_silent_no_results(self):
        self.catalog['code'] = 500
        with self.assertRaises(lcsc_api.LCSCAPIError):
            self.client.search('resistor')
        self.request.side_effect = lcsc_api.LCSCAPIError('Network unavailable')
        with self.assertRaisesRegex(lcsc_api.LCSCAPIError, 'Network unavailable'):
            self.client.search('resistor')

    def test_search_pagination_and_package_are_forwarded(self):
        self.client.search('10k', package='0603', page=2)
        payload = self.request.call_args.args[3]
        self.assertEqual(payload['keyword'], '10k 0603')
        self.assertEqual(payload['currentPage'], 2)

    def test_ambiguous_or_similar_mpn_is_never_silently_selected(self):
        with patch.object(lcsc_api, 'get_lcsc_client', return_value=self.client):
            self.assertIsNone(lcsc_api.get_lcsc_part_data('0603WAF1002T5'))
            duplicate = copy.deepcopy(self.catalog['data']['componentPageInfo']['list'][0])
            duplicate['componentCode'] = 'C9999'
            self.catalog['data']['componentPageInfo']['list'].append(duplicate)
            self.assertIsNone(lcsc_api.get_lcsc_part_data('0603WAF1002T5E'))

    def test_form_preserves_internal_fields_and_uses_kicad_value_format(self):
        def widget(value):
            instance = Mock()
            instance.get.return_value = value
            return instance
        app = App.__new__(App)
        app.current_table = 'Resistor'
        app.status_var = Mock()
        names = ('Manufacturer_Part_Number', 'Value', 'Company_Part_Number', 'Company_Stock',
                 'Verified', 'Symbols', 'Footprints', 'Created_At')
        app.entry_widgets = {name: widget('keep') for name in names}
        writes = {}
        with patch('Company_Kicad_DB.widget_set_value', side_effect=lambda w, v: writes.update({next(k for k, item in app.entry_widgets.items() if item is w): v})):
            app.fill_form_from_lcsc_data(self.product())
        self.assertEqual(set(writes), {'Manufacturer_Part_Number', 'Value'})
        self.assertEqual(writes['Value'], '10kΩ')


class TestLCSCLibrary(LcscFixture):
    def test_flat_download_has_no_subfolders_and_links_models_in_selected_folder(self):
        target = self.directory / 'all-files'

        def model(raw, info, output):
            output.mkdir(parents=True)
            step = output / 'C25804.step'
            step.write_bytes(b"ISO-10303-21;HEADER;FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));ENDSEC;END-ISO-10303-21;")
            return {'step': step}

        with patch.object(lcsc_library.Model3DConverter, 'process_component_model', side_effect=model):
            result = lcsc_library.import_component(self.product(), str(target), register=False, flat=True)
        self.assertTrue(all(p.is_file() for p in target.iterdir()))
        self.assertTrue(all(Path(p).parent == target for p in result['files']))
        self.assertTrue((target / 'C25804.step').is_file())
        footprint = next(target.glob('*.kicad_mod'))
        self.assertIn(f'{target.as_posix()}/C25804.step', footprint.read_text(encoding='utf-8'))
        original = footprint.read_bytes()
        # A deliberately edited footprint is backed up alongside the files on replacement.
        edited = original + b'\n; user note\n'
        footprint.write_bytes(edited)
        lcsc_library.import_component(self.product(), str(target), model=False, register=False, flat=True, replace=True)
        self.assertTrue(all(p.is_file() for p in target.iterdir()))
        self.assertTrue(any(p.read_bytes() == edited for p in target.glob('*.bak')))

    def test_actual_converters_write_usable_symbol_and_footprint_without_placeholders(self):
        target = self.directory / 'library'
        result = lcsc_library.import_component(self.product(), str(target), register=False, model=False)
        self.assertIn('Company_LCSC:C25804_', result['fields']['Symbols'])
        self.assertIn('Company_LCSC:C25804_', result['fields']['Footprints'])
        symbol_path = target / 'Company_LCSC.kicad_sym'
        self.assertEqual(len(symbol_lib.list_symbols(symbol_path)), 1)
        self.assertIn('(pin ', symbol_path.read_text(encoding='utf-8'))
        footprint = next((target / 'Footprints.pretty').glob('*.kicad_mod'))
        self.assertIn('(pad ', footprint.read_text(encoding='utf-8'))
        self.assertNotIn('(model ', footprint.read_text(encoding='utf-8'))
        # Reimport reuses a part instead of duplicating or overwriting user edits.
        original = symbol_path.read_bytes()
        repeated = lcsc_library.import_component(self.product(), str(target), register=False, model=False)
        self.assertEqual(symbol_path.read_bytes(), original)
        self.assertTrue(any('reused' in message for message in repeated['warnings']))

    def test_unavailable_model_is_reported_and_footprint_has_no_broken_link(self):
        with patch.object(lcsc_library.Model3DConverter, 'process_component_model', return_value={}):
            target = self.directory / 'library'
            result = lcsc_library.import_component(self.product(), str(target), register=False)
        self.assertTrue(any('No 3D model' in message for message in result['warnings']))
        self.assertFalse((target / '3dmodels' / 'C25804.wrl').exists())
        self.assertNotIn('(model ', next((target / 'Footprints.pretty').glob('*')).read_text(encoding='utf-8'))

    def test_model_paths_and_global_registration_are_consistent(self):
        target = self.directory / 'library'
        settings = self.directory / 'kicad'
        settings.mkdir()
        original = '(sym_lib_table\n (lib (name "Other")(type "KiCad")(uri "C:/Other.kicad_sym")(options "")(descr ""))\n)\n'
        (settings / 'sym-lib-table').write_text(original, encoding='utf-8')

        def model(raw, info, output):
            output.mkdir(parents=True)
            path = output / 'C25804.step'
            path.write_bytes(b'ISO-10303-21;\nEND-ISO-10303-21;')
            return {'step': path}

        with patch.object(lcsc_library.Model3DConverter, 'process_component_model', side_effect=model):
            result = lcsc_library.import_component(self.product(), str(target), kicad_directory=str(settings))
        self.assertTrue(Path(result['models']['step']).is_file())
        self.assertIn('Other', (settings / 'sym-lib-table').read_text(encoding='utf-8'))
        self.assertIn('Company_LCSC', (settings / 'fp-lib-table').read_text(encoding='utf-8'))
        self.assertTrue(list((target / '_backups').rglob('*sym-lib-table')))
        text = next((target / 'Footprints.pretty').glob('*')).read_text(encoding='utf-8')
        self.assertIn('/C25804.step', text)
        self.assertNotIn('/C25804.wrl', text)

    def test_library_name_conflict_leaves_existing_files_untouched(self):
        target = self.directory / 'library'
        settings = self.directory / 'kicad'
        settings.mkdir()
        original = '(sym_lib_table\n (lib (name "Company_LCSC")(type "KiCad")(uri "C:/Other.kicad_sym")(options "")(descr "user library"))\n)\n'
        (settings / 'sym-lib-table').write_text(original, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already has a different'):
            lcsc_library.import_component(self.product(), str(target), model=False, kicad_directory=str(settings))
        self.assertEqual((settings / 'sym-lib-table').read_text(encoding='utf-8'), original)
        self.assertFalse(list(target.glob('*.kicad_sym')))
        self.assertFalse(list(target.rglob('*.kicad_mod')))

    def test_model_only_download_fills_missing_step_and_preserves_existing_wrl(self):
        target = self.directory / 'chosen-library'
        models = target / '3dmodels'
        models.mkdir(parents=True)
        original = models / 'C25804.wrl'
        original.write_bytes(b'Existing user model')

        def download(raw, info, output):
            output.mkdir(parents=True)
            step, wrl = output / 'C25804.step', output / 'C25804.wrl'
            step.write_bytes(b"ISO-10303-21;HEADER;FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));ENDSEC;END-ISO-10303-21;")
            wrl.write_bytes(b'New supplier model')
            return {'step': step, 'wrl': wrl}

        with patch.object(lcsc_library.Model3DConverter, 'process_component_model', side_effect=download) as fetch:
            result = lcsc_library.import_component(self.product(), str(target), symbol=False, footprint=False, register=False)
            fetch.assert_called_once()
        self.assertEqual(original.read_bytes(), b'Existing user model')
        self.assertTrue((models / 'C25804.step').is_file())
        self.assertEqual(result['fields'], {})
        self.assertEqual(set(result['files']), {str(original), str(models / 'C25804.step')})
        self.assertFalse(list(target.glob('*.kicad_sym')))
        self.assertFalse((target / 'Footprints.pretty').exists())
        with patch.object(lcsc_library.Model3DConverter, 'process_component_model') as fetch:
            repeated = lcsc_library.import_component(self.product(), str(target), symbol=False, footprint=False, register=False)
            fetch.assert_not_called()
        self.assertEqual(set(repeated['files']), set(result['files']))

    def test_failed_conversion_does_not_create_placeholder_or_modify_destination(self):
        product = self.product()
        product['easyeda_data']['dataStr']['shape'] = []
        product['easyeda_data']['dataStr'].pop('subparts', None)
        target = self.directory / 'library'
        with self.assertRaises(ValueError):
            lcsc_library.import_component(product, str(target), model=False, register=False)
        self.assertFalse(list(target.rglob('*.kicad_mod')))
        self.assertFalse(list(target.rglob('*.kicad_sym')))

    def test_commit_failure_rolls_back_prior_files(self):
        a, b = self.directory / 'a', self.directory / 'b'
        a.write_bytes(b'original')
        original_write = lcsc_library._atomic_write

        def write(path, data):
            if path == b:
                raise OSError('Simulated disk failure')
            original_write(path, data)

        with patch.object(lcsc_library, '_atomic_write', side_effect=write), self.assertRaises(OSError):
            lcsc_library._commit({a: b'new', b: b'new'}, {a: b'original', b: None})
        self.assertEqual(a.read_bytes(), b'original')
        self.assertFalse(b.exists())

    def test_invalid_part_and_paths_rejected_before_writing(self):
        product = self.product()
        product['lcsc_id'] = '../../escape'
        with self.assertRaises(lcsc_api.LCSCAPIError):
            lcsc_library.import_component(product, str(self.directory), register=False)
        with self.assertRaises(ValueError):
            lcsc_library.import_component(self.product(), 'relative/path', register=False)


class TestLCSCWindow(LcscFixture):
    def test_search_select_download_to_chosen_folder_and_separate_form_callback(self):
        import gc
        import time
        import tkinter as tk
        from ui.lcsc_manager_window import LcscManagerWindow
        root = tk.Tk()
        root.withdraw()
        callback = Mock()
        window = None
        try:
            with patch('ui.lcsc_manager_window.messagebox.showinfo'), \
                    patch('ui.lcsc_manager_window.messagebox.showerror') as error, \
                    patch('ui.step_preview.load_component_step', side_effect=ValueError('No test STEP model')):
                window = LcscManagerWindow(root, callback, 'C25804')
                window.withdraw()

                def wait_for(predicate):
                    deadline = time.monotonic() + 6
                    while not predicate() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertTrue(predicate())

                wait_for(lambda: window.product is not None and not window.busy)
                self.assertEqual(window.tree.selection(), ('C25804',))
                self.assertIn('10kΩ', window.details.get('1.0', 'end'))
                self.assertEqual([window.preview_tabs.tab(t,'text') for t in window.preview_tabs.tabs()], ['CAD previews','Specifications'])
                self.assertEqual(len(window.cad_panes.panes()), 3)
                self.assertTrue(window.symbol_preview.scenes)
                self.assertTrue(window.footprint_preview.scenes)
                window.apply_details()
                self.assertEqual(callback.call_args.args[1], {})
                callback.reset_mock()
                window.destination.set(str(self.directory / 'library'))
                window.model.set(False)
                with patch.object(lcsc_library, 'ensure_lib_entry', side_effect=AssertionError('Unexpected library registration')):
                    window.download_selected()
                    wait_for(lambda: not window.busy)
                callback.assert_not_called()
                self.assertTrue((self.directory / 'library' / 'Company_LCSC.kicad_sym').is_file())
                self.assertTrue(list((self.directory / 'library').glob('*.kicad_mod')))
                self.assertTrue(all(path.is_file() for path in (self.directory / 'library').iterdir()))
                labels = [button.cget('text') for button in window.buttons]
                self.assertIn('Download CAD files', labels)
                self.assertNotIn('Import libraries and fill form', labels)
                error.assert_not_called()
                wait_for(lambda: not window.step_preview.load_busy)
        finally:
            if window:
                window.close()
            root.destroy()
            gc.collect()

    def test_main_button_opens_manager_even_when_a_bom_is_selected(self):
        app = App.__new__(App)
        app.root = Mock()
        app.is_busy = lambda: False
        app.bom_file_path = 'previous-bom.xlsx'
        app.search_var = Mock()
        app.search_var.get.return_value = 'C25804'
        app.entry_widgets = {}
        with patch('ui.lcsc_manager_window.LcscManagerWindow') as window:
            app.lcsc_search_records()
        self.assertEqual(window.call_args.args[2], 'C25804')


if __name__ == '__main__':
    unittest.main()
