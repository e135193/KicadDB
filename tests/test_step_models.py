from pathlib import Path
import tempfile
import unittest
import json
import copy
from unittest.mock import patch

from step_models import step_protocol, local_step_files, file_protocol
from step_preview import load_component_step
import lcsc_library
import lcsc_api
import lcsc_settings
from api_keys import ApiKeyManager


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


SCHEMAS = {'AP242': 'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF',
           'AP214': 'AUTOMOTIVE_DESIGN', 'AP203': 'CONFIG_CONTROL_DESIGN'}


def data(protocol):
    return f"ISO-10303-21;HEADER;FILE_SCHEMA(('{SCHEMAS[protocol]}'));ENDSEC;DATA;ENDSEC;END-ISO-10303-21;".encode()


class TestStepProtocols(unittest.TestCase):
    def test_schema_priority_uses_content_and_exact_component_not_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, protocol in (('C123.step','AP203'), ('C123_AP203.stp','AP242'), ('C123_AP242.step','AP214'), ('C1234.step','AP242')):
                (root/name).write_bytes(data(protocol))
            self.assertEqual([file_protocol(p) for p in local_step_files(root, 'C123')], ['AP242','AP214','AP203'])
            (root/'C123_AP203.stp').unlink()
            self.assertEqual([file_protocol(p) for p in local_step_files(root, 'C123')], ['AP214','AP203'])
        self.assertEqual(step_protocol(b"ISO-10303-21;HEADER;/*FILE_SCHEMA(('AP242'));*/FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));ENDSEC;"), 'AP203')
        self.assertEqual(step_protocol(b"FILE_DESCRIPTION(('AP242'), '2;1');"), 'Unknown')

    def test_preview_prefers_local_ap242_without_download_and_falls_back_on_bad_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'C123_AP242.step').write_bytes(data('AP242'))
            (root/'C123.step').write_bytes(data('AP203'))
            with patch('step_preview.read_step', return_value='mesh') as read, \
                    patch('step_preview.Model3DConverter._download_step') as fetch:
                self.assertEqual(load_component_step({'lcsc_id':'C123'},folder),'mesh')
                self.assertEqual(read.call_args.args[0].name,'C123_AP242.step')
                fetch.assert_not_called()
            def read_model(path):
                if file_protocol(path) == 'AP242':
                    raise ValueError('Invalid geometry')
                return file_protocol(path)
            with patch('step_preview.read_step', side_effect=read_model), \
                    patch('step_preview.Model3DConverter._extract_3d_model_info', return_value={'uuid':'test'}), \
                    patch('step_preview.Model3DConverter._download_step', return_value=data('AP214')):
                self.assertEqual(load_component_step({'lcsc_id':'C123'},folder),'AP214')


class TestStepDownloadPriority(LcscFixture):
    def test_new_ap242_preserves_old_ap203_and_links_preferred_file(self):
        root = self.directory / 'library'
        root.mkdir()
        old = root/'C25804.step'
        old.write_bytes(data('AP203'))
        def model(raw, info, output):
            output.mkdir()
            path=output/'C25804.step'
            path.write_bytes(data('AP242'))
            return {'step':path}
        with patch.object(lcsc_library.Model3DConverter,'process_component_model',side_effect=model):
            result=lcsc_library.import_component(self.product(),str(root),symbol=False,register=False,flat=True)
        self.assertEqual(old.read_bytes(),data('AP203'))
        self.assertEqual(Path(result['models']['step']).name,'C25804_AP242.step')
        self.assertIn('/C25804_AP242.step',next(root.glob('*.kicad_mod')).read_text())
        self.assertTrue(all(p.is_file() for p in root.iterdir()))

    def test_lower_supplier_protocol_never_downgrades_existing_ap242(self):
        root=self.directory/'library'
        root.mkdir()
        (root/'C25804.step').write_bytes(data('AP242'))
        def model(raw, info, output):
            output.mkdir()
            path=output/'C25804.step'
            path.write_bytes(data('AP203'))
            return {'step':path}
        with patch.object(lcsc_library.Model3DConverter,'process_component_model',side_effect=model):
            result=lcsc_library.import_component(self.product(),str(root),symbol=False,footprint=False,register=False,flat=True,replace=True)
        self.assertEqual(file_protocol(result['models']['step']),'AP242')
