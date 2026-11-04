"""Read real STEP solids and render a read-only, orthographic preview."""
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
import struct
import zlib

import numpy as np

from lcsc_api import valid_lcsc_id
from step_models import file_protocol, local_step_files, rank_step_files
from third_party.lcsc_manager.converters.model_3d_converter import (
    Model3DConverter, ENDPOINT_3D_MODEL_STEP,
)

_STEP_LOCK = threading.Lock()


@dataclass(frozen=True)
class StepMesh:
    triangles: np.ndarray
    normals: np.ndarray
    size: tuple
    summary: str
    colors: np.ndarray | None = None  # per-triangle sRGB, in [0, 1]


def read_step(path, name=None):
    """Tessellate STEP in millimetres; no persistent files or GUI operations."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_ShapeTool
        from OCP.XCAFPrs import XCAFPrs, XCAFPrs_DocumentExplorer, XCAFPrs_IndexedDataMapOfShapeStyle
        from OCP.Quantity import Quantity_TOC_sRGB
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRep import BRep_Tool
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopoDS import TopoDS, TopoDS_Iterator
        from OCP.TopLoc import TopLoc_Location
    except ImportError as exc:
        raise RuntimeError('STEP preview requires cadquery-ocp. Install the application requirements or use the updated EXE.') from exc
    path = Path(path)
    if path.stat().st_size > 80 * 1024 * 1024:
        raise ValueError('This STEP file exceeds the 80 MB preview limit.')
    with _STEP_LOCK:
        reader = STEPCAFControl_Reader()
        reader.SetColorMode(True)
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ValueError('The file does not contain a readable STEP model.')
        reader.Reader().SetSystemLengthUnit(1.0)
        document = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
        if not reader.Transfer(document):
            raise ValueError('The file does not contain a readable STEP model.')
        nodes = XCAFPrs_DocumentExplorer(document, 0)
        instances = []
        box = Bnd_Box()
        while nodes.More():
            node = nodes.Current()
            if not node.IsAssembly and node.Style.IsVisible():
                shape = XCAFDoc_ShapeTool.GetShape_s(node.RefLabel)
                if not shape.IsNull():
                    styles = XCAFPrs_IndexedDataMapOfShapeStyle()
                    XCAFPrs.CollectStyleSettings_s(node.RefLabel, TopLoc_Location(), styles)
                    color = node.Style.GetColorSurf().Values(Quantity_TOC_sRGB) if node.Style.IsSetColorSurf() else (.66, .66, .66)
                    # DocumentExplorer supplies the absolute instance placement;
                    # shape/face triangulations still use definition coordinates.
                    placement = node.Location * shape.Location().Inverted()
                    instances.append((shape, styles, color, placement))
                    BRepBndLib.Add_s(shape.Moved(placement), box)
            nodes.Next()
        if box.IsVoid():
            raise ValueError('The STEP model has no visible geometry.')
        low_corner, high_corner = box.CornerMin().Coord(), box.CornerMax().Coord()
        span = max(high_corner[i] - low_corner[i] for i in range(3))
        cast_face = getattr(TopoDS, 'Face_s', None) or TopoDS.Face
        triangles, colors = [], []

        def collect(shape, styles, color, placement, root=False):
            # Face overrides inherit body colours. Root style already includes
            # assembly-instance overrides resolved by DocumentExplorer.
            if not root and styles.Contains(shape):
                style = styles.FindFromKey(shape)
                if not style.IsVisible():
                    return
                if style.IsSetColorSurf():
                    color = style.GetColorSurf().Values(Quantity_TOC_sRGB)
            if shape.ShapeType() != TopAbs_FACE:
                children = TopoDS_Iterator(shape)
                while children.More():
                    collect(children.Value(), styles, color, placement)
                    children.Next()
                return
            face = cast_face(shape)
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is not None:
                transform = (placement * location).Transformation()
                points = [mesh.Node(i).Transformed(transform).Coord() for i in range(1, mesh.NbNodes() + 1)]
                for i in range(1, mesh.NbTriangles() + 1):
                    indices = mesh.Triangle(i).Get()
                    if face.Orientation() == TopAbs_REVERSED:
                        indices = tuple(reversed(indices))
                    triangles.append([points[j - 1] for j in indices])
                    colors.append(color)
                if len(triangles) > 150000:
                    raise ValueError('This STEP model is too detailed for the preview. Open it in a CAD application.')
        for shape, styles, color, placement in instances:
            BRepMesh_IncrementalMesh(shape, max(.005, span * .002), False, .35, False)
            collect(shape, styles, color, placement, root=True)
    if not triangles:
        raise ValueError('The STEP model contains no surfaces to display.')
    geometry = np.asarray(triangles, dtype=float)
    if not np.isfinite(geometry).all():
        raise ValueError('The STEP model contains invalid coordinates.')
    low, high = geometry.min(axis=(0, 1)), geometry.max(axis=(0, 1))
    geometry -= (low + high) / 2
    normals = np.cross(geometry[:, 1] - geometry[:, 0], geometry[:, 2] - geometry[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-12
    geometry, normals = geometry[valid], normals[valid] / lengths[valid, None]
    if not len(geometry):
        raise ValueError('The STEP model contains only degenerate surfaces.')
    size = tuple(high - low)
    dimensions = ' × '.join(f'{v:.2f}' for v in size)
    return StepMesh(geometry, normals, size, f'{name or path.name} · {file_protocol(path)} · {dimensions} mm',
                    np.asarray(colors, dtype=float)[valid])


def load_component_step(product, folder):
    """Choose AP242 > AP214 > AP203 from actual local and supplier files."""
    code = valid_lcsc_id(product.get('lcsc_id'))
    local = local_step_files(Path(folder).expanduser() if folder else '', code)
    remaining = []
    for path in local:
        if file_protocol(path) == 'AP242':
            try:
                return read_step(path)
            except (ValueError, OSError):
                continue
        remaining.append(path)
    converter = Model3DConverter()
    info = converter._extract_3d_model_info(product.get('easyeda_data') or {})
    data = converter._download_step(ENDPOINT_3D_MODEL_STEP.format(uuid=info['uuid'])) if info else None
    error = 'The STEP model could not be downloaded. Check the connection and try again.' if info else 'No 3D model is available for this component.'
    if data and (len(data) > 80 * 1024 * 1024 or b'ISO-10303-21' not in data[:4096]):
        data = None
        error = 'The supplier did not return a supported STEP file.'
    with tempfile.TemporaryDirectory(prefix='lcsc-step-preview-') as directory:
        if data:
            path = Path(directory) / f'{code}.step'
            path.write_bytes(data)
            remaining.append(path)
        for path in rank_step_files(remaining):
            try:
                return read_step(path)
            except (ValueError, OSError) as exc:
                error = str(exc)
    raise ValueError(error)


def render_step(mesh, width, height, zoom=1.0, pan=(0, 0), angles=(-35, 60)):
    """Render source colours with per-pixel depth (including intersecting faces)."""
    width, height = max(1, int(width)), max(1, int(height))
    antialias = 2 if width * height <= 800000 else 1
    w, h = width * antialias, height * antialias
    yaw, pitch = np.radians(angles)
    c, s, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    rotation = np.array(((c, -s, 0), (cp*s, cp*c, -sp), (sp*s, sp*c, cp)))
    geometry = mesh.triangles @ rotation.T
    normals = mesh.normals @ rotation.T
    radius = max(np.linalg.norm(mesh.size) / 2, .001)
    scale = min(w, h) * .42 / radius * zoom
    xy = geometry[:, :, :2] * (scale, -scale) + (w/2 + pan[0]*antialias, h/2 + pan[1]*antialias)
    base = np.clip(mesh.colors if mesh.colors is not None else np.full((len(geometry), 3), .66), 0, 1)
    linear = np.where(base <= .04045, base / 12.92, ((base + .055) / 1.055) ** 2.4)
    light = np.array((.25, -.35, .9))
    light /= np.linalg.norm(light)
    shade = .35 + .65 * np.abs(normals @ light)
    lit = np.clip(linear * shade[:, None] + .07 * np.abs(normals[:, 2:3]) ** 24, 0, 1)
    colors = np.rint(255 * np.where(lit <= .0031308, lit * 12.92, 1.055 * lit ** (1/2.4) - .055)).astype(np.uint8)
    pixels = np.empty((h, w, 3), dtype=np.uint8)
    pixels[:] = (245, 247, 250)
    depth = np.full((h, w), -np.inf)
    for index in range(len(geometry)):
        points = xy[index]
        x0, y0 = np.maximum(np.floor(points.min(axis=0)), 0).astype(int)
        x1, y1 = np.minimum(np.ceil(points.max(axis=0)), (w, h)).astype(int)
        if x1 <= x0 or y1 <= y0:
            continue
        a, b, c = points
        area = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
        if abs(area) < 1e-10:
            continue
        xs = np.arange(x0, x1)[None, :] + .5
        z = geometry[index, :, 2]
        # Bounded strips avoid large temporary arrays at high zoom levels.
        for start in range(y0, y1, 64):
            end = min(start + 64, y1)
            ys = np.arange(start, end)[:, None] + .5
            u = ((b[1]-c[1])*(xs-c[0]) + (c[0]-b[0])*(ys-c[1])) / area
            v = ((c[1]-a[1])*(xs-c[0]) + (a[0]-c[0])*(ys-c[1])) / area
            t = 1-u-v
            surface_z = u*z[0] + v*z[1] + t*z[2]
            target = depth[start:end, x0:x1]
            visible = (u >= -1e-9) & (v >= -1e-9) & (t >= -1e-9) & (surface_z > target + 1e-10)
            target[visible] = surface_z[visible]
            pixels[start:end, x0:x1][visible] = colors[index]
    if antialias == 2:
        pixels = pixels.reshape(height, 2, width, 2, 3).mean(axis=(1, 3)).astype(np.uint8)
    # RGB PNG, without adding a second imaging dependency to packaged builds.
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = np.zeros((height, width*3 + 1), dtype=np.uint8)
    raw[:, 1:] = pixels.reshape(height, width*3)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw.tobytes(), 3)) + chunk(b'IEND', b''))
