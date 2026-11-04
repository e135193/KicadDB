from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
import io
import numpy as np
from unittest.mock import Mock, patch

import step_preview
from ui.step_preview import StepPreview


class TestStepPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        from OCP.IFSelect import IFSelect_RetDone
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.path = Path(cls.temp.name) / 'test.step'
        writer = STEPControl_Writer()
        writer.Transfer(BRepPrimAPI_MakeBox(2, 3, 4).Shape(), STEPControl_AsIs)
        assert writer.Write(str(cls.path)) == IFSelect_RetDone
        cls.mesh = step_preview.read_step(cls.path)

    def test_real_step_geometry_dimensions_and_rotation(self):
        for actual, expected in zip(self.mesh.size, (2, 3, 4)):
            self.assertAlmostEqual(actual, expected, places=4)
        front = step_preview.render_step(self.mesh, 400, 300, angles=(0, 90))
        top = step_preview.render_step(self.mesh, 400, 300, angles=(0, 0))
        self.assertTrue(front.startswith(b'\x89PNG'))
        self.assertNotEqual(front, top)
        # Geometry-only STEP files use a neutral fallback, not a blue tint.
        np.testing.assert_allclose(self.mesh.colors, .66)

    def test_step_body_and_face_colours_are_preserved_in_srgb(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.STEPCAFControl import STEPCAFControl_Writer
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorSurf
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_FACE
        doc = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
        shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        shape = BRepPrimAPI_MakeBox(2, 3, 4).Shape()
        label = shapes.AddShape(shape, False)
        red, green = (.8, .2, .1), (.1, .7, .3)
        colors.SetColor(label, Quantity_Color(*red, Quantity_TOC_sRGB), XCAFDoc_ColorSurf)
        face = TopExp_Explorer(shape, TopAbs_FACE).Current()
        colors.SetColor(shapes.AddSubShape(label, face), Quantity_Color(*green, Quantity_TOC_sRGB), XCAFDoc_ColorSurf)
        writer = STEPCAFControl_Writer()
        self.assertTrue(writer.Transfer(doc))
        path = self.path.parent / 'colored.step'
        writer.Write(str(path))
        mesh = step_preview.read_step(path)
        distinct = np.unique(mesh.colors.round(4), axis=0)
        np.testing.assert_allclose(distinct, [green, red], atol=1e-4)
        self.assertEqual(mesh.size, self.mesh.size)

    def test_coloured_assembly_instances_keep_locations_and_overrides(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.STEPCAFControl import STEPCAFControl_Writer
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorSurf
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
        from OCP.TopLoc import TopLoc_Location
        from OCP.gp import gp_Trsf, gp_Vec
        doc = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
        shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        part = shapes.AddShape(BRepPrimAPI_MakeBox(2, 3, 4).Shape(), False)
        colors.SetColor(part, Quantity_Color(1., 0., 0., Quantity_TOC_sRGB), XCAFDoc_ColorSurf)
        assembly = shapes.NewShape()
        shapes.AddComponent(assembly, part, TopLoc_Location())
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(10, 0, 0))
        instance = shapes.AddComponent(assembly, part, TopLoc_Location(move))
        colors.SetColor(instance, Quantity_Color(0., 0., 1., Quantity_TOC_sRGB), XCAFDoc_ColorSurf)
        shapes.UpdateAssemblies()
        writer = STEPCAFControl_Writer()
        self.assertTrue(writer.Transfer(doc))
        path = self.path.parent / 'instances.step'
        writer.Write(str(path))
        mesh = step_preview.read_step(path)
        np.testing.assert_allclose(mesh.size, (12, 3, 4), atol=1e-5)
        left = mesh.triangles[:, :, 0].mean(axis=1) < 0
        np.testing.assert_allclose(mesh.colors[left], np.tile((1, 0, 0), (left.sum(), 1)), atol=1e-5)
        np.testing.assert_allclose(mesh.colors[~left], np.tile((0, 0, 1), ((~left).sum(), 1)), atol=1e-5)

    def test_crossing_faces_use_pixel_depth_independently_of_draw_order(self):
        from PIL import Image
        # Same projected triangle: red slopes through a flat blue triangle.
        geometry = np.array([[[-2,-2,-1], [2,-2,1], [0,2,0]],
                             [[-2,-2,0], [2,-2,0], [0,2,0]]], dtype=float)
        normals = np.cross(geometry[:,1]-geometry[:,0], geometry[:,2]-geometry[:,0])
        normals /= np.linalg.norm(normals, axis=1)[:,None]
        mesh = step_preview.StepMesh(geometry, normals, (4,4,2), '', np.array([[1,0,0],[0,0,1]]))
        png = step_preview.render_step(mesh, 400, 400, angles=(0,0))
        picture = np.asarray(Image.open(io.BytesIO(png)))
        self.assertGreater(picture[230,170,2], picture[230,170,0])
        self.assertGreater(picture[230,230,0], picture[230,230,2])
        reverse = step_preview.StepMesh(geometry[::-1], normals[::-1], mesh.size, '', mesh.colors[::-1])
        other = np.asarray(Image.open(io.BytesIO(step_preview.render_step(reverse,400,400,angles=(0,0)))))
        np.testing.assert_array_equal(picture, other)

    def test_local_step_is_used_without_network_and_preview_download_leaves_no_files(self):
        product = {'lcsc_id': 'C123', 'easyeda_data': {}}
        local = self.path.parent / 'C123.step'
        local.write_bytes(self.path.read_bytes())
        with patch.object(step_preview.Model3DConverter, '_download_step') as fetch:
            result = step_preview.load_component_step(product, str(local.parent))
            fetch.assert_not_called()
        self.assertIn('C123.step', result.summary)
        local.unlink()
        with patch.object(step_preview.Model3DConverter, '_extract_3d_model_info', return_value={'uuid':'test'}), \
                patch.object(step_preview.Model3DConverter, '_download_step', return_value=self.path.read_bytes()):
            result = step_preview.load_component_step(product, str(local.parent))
        self.assertFalse(local.exists())
        self.assertEqual(result.size, self.mesh.size)

    def test_missing_invalid_and_unreadable_models_are_reported(self):
        product = {'lcsc_id': 'C123'}
        with self.assertRaisesRegex(ValueError, 'No 3D model'):
            step_preview.load_component_step(product, '')
        with patch.object(step_preview.Model3DConverter, '_extract_3d_model_info', return_value={'uuid':'test'}), \
                patch.object(step_preview.Model3DConverter, '_download_step', return_value=b'<html>Error</html>'):
            with self.assertRaisesRegex(ValueError, 'supported STEP'):
                step_preview.load_component_step(product, '')
        invalid = self.path.parent / 'invalid.step'
        invalid.write_text('not a STEP file', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'readable STEP'):
            step_preview.read_step(invalid)

    def test_widget_renders_rotates_queues_new_component_and_discards_closed_results(self):
        root = tk.Tk()
        root.withdraw()
        product = {'lcsc_id': 'C1'}
        panel = StepPreview(root, lambda: product, lambda: '')
        panel.pack()
        release = threading.Event()

        def wait_for(predicate):
            deadline = time.monotonic() + 8
            while not predicate() and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            self.assertTrue(predicate())

        def load(part, folder):
            if part['lcsc_id'] == 'C1':
                release.wait(4)
            return self.mesh

        try:
            with patch('ui.step_preview.load_component_step', side_effect=load) as fetch, \
                    patch.object(panel.canvas, 'winfo_width', return_value=400), \
                    patch.object(panel.canvas, 'winfo_height', return_value=300):
                panel.load_component(force=False)
                product = {'lcsc_id': 'C2'}
                panel.reset_component()
                panel.load_component(force=False)
                release.set()
                wait_for(lambda: panel.loaded_code == 'C2' and panel.photo is not None)
                self.assertEqual(fetch.call_count, 2)
                panel.load_component(force=False)
                self.assertEqual(fetch.call_count, 2)
                panel._start_drag(Mock(x=20, y=20))
                panel._drag(Mock(x=50, y=40))
                self.assertNotEqual(panel.angles, (-35, 60))
                panel.set_view((0, 0))
                self.assertEqual(panel.angles, (0, 0))
                panel.change_zoom(2)
                self.assertEqual(panel.zoom, 2)
                wait_for(lambda: not panel.busy)
            panel.destroy()
            panel.load_events.put((panel.load_version, self.mesh, None))
            root.update()
            self.assertTrue(panel.closed)
        finally:
            release.set()
            root.destroy()
