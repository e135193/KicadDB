import os
import tempfile
import unittest
from unittest.mock import Mock, patch
import pandas as pd
import Company_Kicad_DB as module


class TestBomDigikeyExport(unittest.TestCase):
    def test_bom_button_exports_live_stocks_and_six_price_tiers(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, 'BOM.xlsx')
            pd.DataFrame({'MPN': ['TEST-PART', 'MISSING'], 'Company Part Number': ['CMP-1', 'CMP-2'],
                          'In-Stock': [999, 999], '1 Adet': [999, 999]}).to_excel(source, index=False)
            with open(source, 'rb') as file:
                original = file.read()
            product = {'ProductVariations': [{'PackageType': {'Name': 'Cut Tape (CT)'},
                        'QuantityAvailable': 42, 'StandardPricing': [
                            {'BreakQuantity': qty, 'UnitPrice': price}
                            for qty, price in zip((1, 10, 100, 250, 500, 1000), (6, 5, 4, 3, 2, 1))]}]}
            app = module.App.__new__(module.App)
            app.bom_file_label_var = Mock()
            app.status_var = Mock()
            def run(worker, on_success, on_error=None, **kwargs):
                on_success(worker(Mock(), lambda: False))
            app.run_in_background = run
            with patch.object(module.filedialog, 'askopenfilename', return_value=source), \
                 patch.object(module, 'get_digikey_part_data', side_effect=[product, None]), \
                 patch('bom_stock.fetch_selected_stock', side_effect=[0, 12]), \
                 patch.object(module, 'messagebox') as dialogs:
                app.select_bom_file()
                dialogs.showerror.assert_not_called()
            output = pd.read_excel(os.path.join(folder, 'BOM_DigiKey_Updated.xlsx'))
            self.assertEqual(output.at[0, 'Digikey Stock'], 42)
            self.assertEqual(output.at[0, 'Company Stock QTY'], 0)
            self.assertEqual(list(output.columns).index('Company Stock QTY'), list(output.columns).index('Digikey Stock') + 1)
            for qty, price in zip((1, 10, 100, 250, 500, 1000), (6, 5, 4, 3, 2, 1)):
                self.assertEqual(output.at[0, f'{qty} pices'], price)
                self.assertTrue(pd.isna(output.at[1, f'{qty} pices']))
            self.assertTrue(pd.isna(output.at[1, 'Digikey Stock']))
            self.assertEqual(output.at[1, 'Part Status'], 'Not Found')
            with open(source, 'rb') as file:
                self.assertEqual(file.read(), original)


if __name__ == '__main__':
    unittest.main()
