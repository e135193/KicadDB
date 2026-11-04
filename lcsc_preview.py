"""Read-only 2D previews of the same KiCad geometry used by LCSC imports."""
from dataclasses import dataclass
from html import escape
import math
import re

from third_party.lcsc_manager.converters.symbol_converter import SymbolConverter
from third_party.lcsc_manager.converters.footprint_converter import FootprintConverter

FOOTPRINT_BACKGROUND = '#001023'
FOOTPRINT_COLORS = {
    'F.Cu': '#c83434', 'B.Cu': '#4d7fc4',
    'F.SilkS': '#f2eda1', 'B.SilkS': '#b2b7bd',
    'F.Fab': '#afafaf', 'B.Fab': '#646464',
    'F.CrtYd': '#ff26e2', 'B.CrtYd': '#4d7fc4',
    'Edge.Cuts': '#e3b72e', 'Dwgs.User': '#afafaf',
}

def parse_sexpr(text):
    if len(text) > 4_000_000:
        raise ValueError('CAD data is too large to preview.')
    stack, result = [], []
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text):
        if token == '(':
            node = []
            (stack[-1] if stack else result).append(node)
            stack.append(node)
            if len(stack) > 100:
                raise ValueError('CAD nesting is too deep.')
        elif token == ')':
            if not stack:
                raise ValueError('Unbalanced CAD data.')
            stack.pop()
        else:
            if not stack:
                raise ValueError('Invalid CAD data.')
            stack[-1].append(re.sub(r'\\(["\\])', r'\1', token[1:-1]) if token.startswith('"') else token)
    if stack or len(result) != 1:
        raise ValueError('Incomplete CAD data.')
    return result[0]


def children(node, key):
    return [item for item in node if isinstance(item, list) and item and item[0] == key]


def child(node, key):
    return next(iter(children(node, key)), [])


def numbers(node, key, default=()):
    values = child(node, key)[1:]
    result = tuple(float(v) for v in values) if values else tuple(default)
    if not all(math.isfinite(v) and abs(v) < 1e7 for v in result):
        raise ValueError('Invalid CAD coordinates.')
    return result


def value(node, key, default=''):
    item = child(node, key)
    return item[1] if len(item) > 1 else default


def rotate(point, angle):
    x, y = point
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return x * c - y * s, x * s + y * c


@dataclass
class Preview:
    body: str
    bounds: tuple
    summary: str
    background: str = '#ffffff'
    grid: bool = False


class Drawing:
    def __init__(self, symbol=False):
        self.elements, self.points = [], []
        self.symbol = symbol

    def point(self, p):
        return p[0], -p[1] if self.symbol else p[1]

    def path(self, points, color='#8c2633', fill='none', width=.15, close=False):
        if len(points) < 2:
            return
        points = [self.point(p) for p in points]
        self.points.extend(points)
        coordinates = ' '.join(f'{x:.6g},{y:.6g}' for x, y in points)
        tag = 'polygon' if close else 'polyline'
        self.elements.append(f'<{tag} points="{coordinates}" stroke="{color}" fill="{fill}" stroke-width="{max(width,.04):.6g}" stroke-linejoin="round"/>')

    def circle(self, center, radius, color='#8c2633', fill='none', width=.15):
        x, y = self.point(center)
        self.points.extend([(x-radius, y-radius), (x+radius, y+radius)])
        self.elements.append(f'<circle cx="{x:.6g}" cy="{y:.6g}" r="{radius:.6g}" stroke="{color}" fill="{fill}" stroke-width="{max(width,.04):.6g}"/>')

    def text(self, text, position, size=.8, color='#175c39', anchor='middle'):
        if not text or text == '~':
            return
        x, y = self.point(position)
        text = str(text)[:120]
        half = len(text)*size*.3
        left, right = (0, 2*half) if anchor == 'start' else ((-2*half, 0) if anchor == 'end' else (-half, half))
        self.points.extend([(x+left, y-size*.6), (x+right, y+size*.6)])
        self.elements.append(f'<text x="{x:.6g}" y="{y:.6g}" font-size="{size:.6g}" font-family="Arial" fill="{color}" text-anchor="{anchor}" dominant-baseline="central">{escape(text)}</text>')

    def finish(self, summary):
        if not self.points:
            raise ValueError('No drawable CAD geometry was found.')
        xs, ys = zip(*self.points)
        x, y, width, height = min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)
        margin = max(width, height, 1)*.08
        return Preview(''.join(self.elements), (x-margin, y-margin, width+2*margin, height+2*margin), summary,
                       '#ffffff' if self.symbol else FOOTPRINT_BACKGROUND, not self.symbol)


def arc_points(start, mid, end):
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
    if abs(d) < 1e-10:
        return [start, mid, end]
    a, b, c = ax*ax+ay*ay, bx*bx+by*by, cx*cx+cy*cy
    ox = (a*(by-cy)+b*(cy-ay)+c*(ay-by))/d
    oy = (a*(cx-bx)+b*(ax-cx)+c*(bx-ax))/d
    angles = [math.atan2(y-oy, x-ox) for x, y in (start, mid, end)]
    span = (angles[2]-angles[0]) % math.tau
    if (angles[1]-angles[0]) % math.tau > span:
        span -= math.tau
    radius = math.hypot(ax-ox, ay-oy)
    count = max(8, int(abs(span)*24))
    return [(ox+radius*math.cos(angles[0]+span*i/count), oy+radius*math.sin(angles[0]+span*i/count)) for i in range(count+1)]


def draw_graphic(drawing, node, transform=lambda p: p, color_override=None):
    kind = str(node[0]).removeprefix('fp_').removeprefix('gr_')
    layer = str(value(node, 'layer'))
    color = '#8c2633' if drawing.symbol else FOOTPRINT_COLORS.get(layer, '#afafaf')
    color = color_override or color
    stroke = child(node, 'stroke')
    width = numbers(stroke or node, 'width', (.12,))[0] or .12
    fill = value(child(node, 'fill'), 'type') or value(node, 'fill')
    fill = '#fff4cf' if fill == 'background' else color if fill in ('solid', 'outline', 'yes') else 'none'
    points = []
    if kind in ('line', 'rect', 'rectangle'):
        a, b = numbers(node, 'start'), numbers(node, 'end')
        points = [a, b] if kind == 'line' else [a, (b[0],a[1]), b, (a[0],b[1])]
    elif kind in ('poly', 'polyline', 'curve', 'bezier'):
        points = [tuple(float(v) for v in p[1:3]) for p in children(child(node, 'pts'), 'xy')]
        if kind in ('curve', 'bezier') and len(points) == 4:
            points = [tuple(sum(math.comb(3,j)*(1-t)**(3-j)*t**j*points[j][axis] for j in range(4)) for axis in (0,1)) for t in [i/48 for i in range(49)]]
    elif kind == 'circle':
        center = numbers(node, 'center')
        radius = numbers(node, 'radius')
        if not radius:
            end = numbers(node, 'end')
            radius = (math.dist(center, end),)
        drawing.circle(transform(center), radius[0], color, fill, width)
        return
    elif kind == 'arc':
        start, end, mid = numbers(node, 'start'), numbers(node, 'end'), numbers(node, 'mid')
        if mid:
            points = arc_points(start, mid, end)
        else:  # Legacy footprint arc: start is the center; end starts the arc.
            angle = numbers(node, 'angle', (0,))[0]
            vector = (end[0]-start[0], end[1]-start[1])
            points = [(start[0]+p[0],start[1]+p[1]) for p in [rotate(vector, angle*i/72) for i in range(73)]]
    elif kind == 'text' and len(node) > 1:
        position = numbers(node, 'at', (0,0))[:2]
        drawing.text(node[1], transform(position))
        return
    if points:
        drawing.path([transform(p) for p in points], color, fill, width, kind in ('rect','rectangle','poly'))


def symbol_previews(text):
    root = parse_sexpr(text)
    symbol = child(root, 'symbol')
    return symbol_node_previews(symbol)


def symbol_node_previews(symbol):
    groups = children(symbol, 'symbol')
    units = sorted({int(str(group[1]).rsplit('_',2)[-2]) for group in groups} - {0}) or [1]
    scenes = []
    for unit in units:
        drawing, pins = Drawing(symbol=True), 0
        nodes = []
        for group in groups:
            unit_id, style = map(int, str(group[1]).rsplit('_',2)[-2:])
            if unit_id not in (0, unit) or style not in (0,1):
                continue
            nodes.extend(n for n in group[2:] if isinstance(n,list))
        # Filled body graphics must sit behind pins and their inside labels.
        for node in nodes:
            if node[0] != 'pin':
                draw_graphic(drawing, node)
        for node in nodes:
            if node[0] == 'pin':
                if 'hide' in node:
                    continue
                pins += 1
                x,y,*angle = numbers(node, 'at', (0,0,0))
                angle = angle[0] if angle else 0
                length = numbers(node, 'length', (2.54,))[0]
                dx,dy = rotate((length,0), angle)
                drawing.path([(x,y),(x+dx,y+dy)], '#b02e32', width=.15)
                if len(node)>2 and 'inverted' in node[2]:
                    drawing.circle((x+dx,y+dy),.35,'#b02e32', '#ffffff')
                number = value(node,'number')
                name = value(node,'name')
                drawing.text(number, (x+dx/2, y+dy/2+.6), .9, '#9d2428')
                if name not in ('', '~', number):
                    nx,ny = rotate((length+.6,0),angle)
                    anchor = 'start' if dx>0.1 else 'end' if dx<-.1 else 'middle'
                    drawing.text(name,(x+nx,y+ny),1,anchor=anchor)
        scenes.append(drawing.finish(f'Unit {unit} · {pins} pins'))
    return scenes


def symbol_library_previews(path, identifier):
    """Resolve an exact library symbol, including KiCad extends chains, read-only."""
    from pathlib import Path
    from third_party.lcsc_manager.library.symbol_lib import _scan
    path = Path(path)
    if path.stat().st_size > 80 * 1024 * 1024:
        raise ValueError('The symbol library is too large to preview.')
    try:
        text = path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        text = path.read_text(encoding='latin-1')
    symbols = {entry.name: entry for entry in _scan(text, str(path))[0]}
    name = identifier if identifier in symbols else identifier.split(':', 1)[-1]

    def resolve(current, seen):
        if current in seen or len(seen) > 20:
            raise ValueError('The symbol has circular or excessive inheritance.')
        if current not in symbols:
            raise ValueError(f'Symbol not found in this library: {current}')
        entry = symbols[current]
        node = parse_sexpr(text[entry.start:entry.end])
        parent = value(node, 'extends')
        if parent:
            base = resolve(parent, seen | {current})
            groups = {str(group[1]).rsplit('_', 2)[-2:][0] + '_' + str(group[1]).rsplit('_', 1)[-1]: group
                      for group in children(base, 'symbol') + children(node, 'symbol')}
            node = ['symbol', current] + list(groups.values())
        return node

    return symbol_node_previews(resolve(name, set()))


def rounded_rect(width, height, radius):
    points=[]
    for cx,cy,start in ((width/2-radius,height/2-radius,0),(-width/2+radius,height/2-radius,90),(-width/2+radius,-height/2+radius,180),(width/2-radius,-height/2+radius,270)):
        points.extend((cx+radius*math.cos(math.radians(start+a)),cy+radius*math.sin(math.radians(start+a))) for a in range(0,91,10))
    return points


def footprint_preview(text):
    root, drawing = parse_sexpr(text), Drawing()
    for node in (n for n in root if isinstance(n,list) and str(n[0]).startswith('fp_')):
        if node[0] != 'fp_text':  # Reference/value fields obscure small pads at preview scale.
            draw_graphic(drawing,node)
    pads=children(root,'pad')
    for pad in pads:
        x,y,*angles=numbers(pad,'at',(0,0,0))
        angle=-(angles[0] if angles else 0)
        width,height=numbers(pad,'size')
        def transform(p):
            px,py=rotate(p,angle)
            return x+px,y+py
        shape=pad[3]
        layers = child(pad, 'layers')[1:]
        color = FOOTPRINT_COLORS['B.Cu'] if 'B.Cu' in layers and 'F.Cu' not in layers else FOOTPRINT_COLORS['F.Cu']
        if pad[2] == 'np_thru_hole':
            color = FOOTPRINT_BACKGROUND
        if shape=='circle':
            drawing.circle((x,y),width/2,color,color,.04)
        elif shape in ('rect','oval','roundrect'):
            radius=min(width,height)/2 if shape=='oval' else min(width,height)*numbers(pad,'roundrect_rratio',(.25,))[0] if shape=='roundrect' else 0
            points=rounded_rect(width,height,radius)
            drawing.path([transform(p) for p in points],color,color,.04,True)
        elif shape=='custom':
            for primitive in children(pad,'primitives'):
                for item in primitive[1:]:
                    if isinstance(item,list):
                        if item[0] == 'gr_poly' and not child(item,'fill'):
                            item = item + [['fill','solid']]
                        draw_graphic(drawing,item,transform,color)
        else:
            raise ValueError(f'Unsupported preview pad shape: {shape}')
        drill=child(pad,'drill')[1:]
        if drill:
            oval=drill[0]=='oval'
            dw=float(drill[1] if oval else drill[0])
            dh=float(drill[2]) if oval else dw
            offset=numbers(child(pad,'drill'),'offset',(0,0))
            hole=[transform((p[0]+offset[0],p[1]+offset[1])) for p in rounded_rect(dw,dh,min(dw,dh)/2)]
            drawing.path(hole,'#e3b72e',FOOTPRINT_BACKGROUND,.03,True)
        drawing.text(pad[1],(x,y),max(.22,min(width,height)*.55),'#b2b7bd')
    return drawing.finish(f'{len(pads)} pads · top view')


def build_previews(product):
    """Run on a worker; no network, database writes or persistent library changes."""
    result={}
    raw=(product or {}).get('easyeda_data')
    for name in ('symbol','footprint'):
        try:
            if not raw:
                raise ValueError('No EasyEDA CAD data is available for this component.')
            if name=='symbol':
                text=SymbolConverter().convert(raw,product,symbol_name='Preview')
                result[name]=symbol_previews(text)
            else:
                text=FootprintConverter().convert(raw,product)
                result[name]=[footprint_preview(text)]
        except Exception as exc:
            result[name]=[]
            result[name+'_error']=f'{name.title()} preview unavailable: {exc}'
    return result


def render_png(preview, width, height, zoom=1, pan=(0,0)):
    # Keep optional preview support from preventing the entire LCSC window opening.
    # A failed import is not cached, so installing the renderer also permits retry.
    try:
        import resvg_py
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            'The CAD preview renderer could not be loaded. Install resvg-py in the '
            'Python environment used to start this application, or use the packaged EXE. '
            'Component search and library import are still available.'
        ) from exc
    width,height=max(1,min(int(width),1800)),max(1,min(int(height),1400))
    x,y,w,h=preview.bounds
    scale=min(width/w,height/h)*zoom
    vw,vh=width/scale,height/scale
    vx,vy=x+w/2-vw/2-pan[0]/scale,y+h/2-vh/2-pan[1]/scale
    underlay = ''
    if preview.grid:
        base = 10 ** math.floor(math.log10(20 / scale))
        spacing = next(base * factor for factor in (1, 2, 5, 10) if base * factor * scale >= 20)
        underlay = (f'<defs><pattern id="grid" width="{spacing}" height="{spacing}" patternUnits="userSpaceOnUse">'
                    f'<circle r="{.65/scale}" fill="#797e62"/></pattern></defs>'
                    f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="url(#grid)"/>'
                    f'<path d="M {vx} 0 H {vx+vw} M 0 {vy} V {vy+vh}" stroke="#c2c2c2" stroke-width="{.7/scale}"/>')
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{vx} {vy} {vw} {vh}">{underlay}{preview.body}</svg>'
    return resvg_py.svg_to_bytes(svg_string=svg,background=preview.background,font_family='Arial')
