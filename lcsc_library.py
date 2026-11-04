"""Import upstream-converted KiCad files with staging and rollback."""
import copy
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime

from lcsc_api import valid_lcsc_id
from step_models import file_protocol, local_step_files, rank_step_files, step_protocol
from third_party.lcsc_manager.converters.symbol_converter import SymbolConverter, sanitize_name
from third_party.lcsc_manager.converters.footprint_converter import FootprintConverter
from third_party.lcsc_manager.converters.model_3d_converter import Model3DConverter
from third_party.lcsc_manager.library import symbol_lib
from third_party.lcsc_manager.library.lib_table import ensure_lib_entry, CONFLICT

NICKNAME = 'Company_LCSC'
_IMPORT_LOCK = threading.Lock()


def kicad_settings_folders():
    base = Path(os.environ.get('KICAD_CONFIG_HOME') or
                str(Path(os.environ.get('APPDATA', str(Path.home() / '.config'))) / 'kicad'))
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and re.fullmatch(r'\d+\.\d+', p.name)),
                  key=lambda p: tuple(map(int, p.name.split('.'))), reverse=True)


def _bytes(path):
    return path.read_bytes() if path.is_file() else None


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.lcsc-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _commit(changes, originals):
    written = []
    try:
        for path, content in changes.items():
            if _bytes(path) != originals[path]:
                raise RuntimeError(f'{path.name} changed during import. Please retry.')
            if content == originals[path]:
                continue
            _atomic_write(path, content)
            written.append(path)
    except Exception:
        for path in reversed(written):
            # Never overwrite a concurrent writer while rolling back our own changes.
            if _bytes(path) == changes[path]:
                if originals[path] is None:
                    path.unlink()
                else:
                    _atomic_write(path, originals[path])
        raise


def _without_models(text):
    """Remove complete model expressions, including their nested transforms."""
    while (start := text.find('(model ')) >= 0:
        depth, quoted, escaped = 0, False, False
        for end in range(start, len(text)):
            char = text[end]
            if escaped:
                escaped = False
            elif char == '\\' and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                depth += (char == '(') - (char == ')')
                if depth == 0:
                    text = text[:start] + text[end + 1:]
                    break
        else:
            raise ValueError('Malformed model reference in converted footprint.')
    return text


def import_component(product, destination, *, symbol=True, footprint=True, model=True,
                     register=True, kicad_directory='', replace=False, flat=False):
    code = valid_lcsc_id(product.get('lcsc_id'))
    if not any((symbol, footprint, model)):
        return {'fields': {}, 'files': [], 'warnings': []}
    raw = product.get('easyeda_data')
    if not raw:
        raise ValueError('This catalog part has no EasyEDA CAD data. Use Load details into form instead.')
    root = Path(destination).expanduser()
    if not destination or not root.is_absolute():
        raise ValueError('Choose an absolute destination folder for KiCad libraries.')
    root = root.resolve()
    if register and (not kicad_directory or not Path(kicad_directory).is_absolute()
                     or not Path(kicad_directory).is_dir()):
        raise ValueError('Choose the KiCad settings folder, or clear Register libraries in KiCad.')
    info = copy.deepcopy(product)
    info['footprint_lib_nickname'] = NICKNAME
    model_directory = root if flat else root / '3dmodels'
    footprint_directory = root if flat else root / 'Footprints.pretty'
    fp_converter = FootprintConverter(model_directory.as_posix())
    fp_name = fp_converter._get_footprint_name(info)
    symbol_path = root / f'{NICKNAME}.kicad_sym'
    fp_path = footprint_directory / f'{fp_name}.kicad_mod'
    warnings, fields = [], {}
    root.mkdir(parents=True, exist_ok=True)
    with _IMPORT_LOCK, tempfile.TemporaryDirectory(prefix='lcsc-import-') as temporary:
        stage = Path(temporary)
        changes, originals = {}, {}

        def capture(path):
            if path not in originals:
                originals[path] = _bytes(path)
            return originals[path]

        def put(path, data):
            capture(path)
            changes[path] = data

        models = {}
        for extension in ('wrl', 'step'):
            path = model_directory / f'{code}.{extension}'
            if capture(path) is not None:
                models[extension] = path
        local_steps = local_step_files(model_directory, code)
        if local_steps:
            models['step'] = local_steps[0]
        if model:
            if len(models) == 2 and not replace and file_protocol(models['step']) == 'AP242':
                warnings.append('Existing 3D model files reused.')
            else:
                converter = Model3DConverter()
                downloaded = dict(converter.process_component_model(raw, info, stage / '3dmodels'))
                incoming_step = downloaded.pop('step', None)
                candidates = ([incoming_step] if incoming_step else [])
                candidates = candidates + local_steps if replace else local_steps + candidates
                if candidates:
                    chosen_step = rank_step_files(candidates)[0]
                    if chosen_step == incoming_step:
                        target = model_directory / f'{code}.step'
                        if capture(target) is not None and not replace:
                            protocol = file_protocol(chosen_step)
                            if protocol != 'Unknown':
                                target = model_directory / f'{code}_{protocol}.step'
                        if capture(target) is None or replace:
                            put(target, chosen_step.read_bytes())
                            models['step'] = target
                        else:
                            warnings.append('Existing STEP file preserved; enable Replace existing files to replace it.')
                    else:
                        models['step'] = chosen_step
                for extension, path in downloaded.items():
                    target = model_directory / f'{code}.{extension}'
                    if capture(target) is not None and not replace:
                        continue
                    put(target, path.read_bytes())
                    models[extension] = target
                if not downloaded and not models:
                    warnings.append('No 3D model could be downloaded. No placeholder model was created.')
                for extension in ('step', 'wrl'):
                    if extension not in models:
                        warnings.append(f'{extension.upper()} model is unavailable or could not be downloaded.')
            if 'step' in models:
                selected = models['step']
                protocol = file_protocol(selected) if selected not in changes else step_protocol(changes[selected])
                warnings.append(f'STEP selected: {protocol}. Preference: AP242 > AP214 > AP203.')
        if footprint:
            if capture(fp_path) is not None and not replace:
                warnings.append('Existing footprint reused; select Replace existing files to regenerate it.')
            else:
                text = fp_converter.convert(raw, info)
                if not models:
                    text = _without_models(text)
                elif 'wrl' not in models:
                    text = text.replace(f'/{code}.wrl"', f'/{models["step"].name}"')
                put(fp_path, text.encode('utf-8'))
            fields['Footprints'] = f'{NICKNAME}:{fp_name}'
        if symbol:
            before = capture(symbol_path)
            staged_symbol = stage / symbol_path.name
            if before is not None:
                staged_symbol.write_bytes(before)
            existing = next((s for s in symbol_lib.list_symbols(staged_symbol) if s.lcsc_id == code), None)
            name = existing.name if existing else f'{code}_{sanitize_name(info.get("manufacturer_part") or info.get("name") or code)}'
            if existing and not replace:
                warnings.append('Existing symbol reused; select Replace existing files to regenerate it.')
            else:
                converter = SymbolConverter()
                text = converter.convert(raw, info, symbol_name=name)
                if not footprint and not fp_path.is_file():
                    text = re.sub(r'(\(property "Footprint" )"(?:\\.|[^"\\])*"', r'\1""', text)
                symbol_lib.put_symbol(staged_symbol, text)
                put(symbol_path, staged_symbol.read_bytes())
            fields['Symbols'] = f'{NICKNAME}:{name}'
        if register:
            for enabled, kind, filename, target in ((symbol, 'sym', 'sym-lib-table', symbol_path),
                                                     (footprint, 'fp', 'fp-lib-table', fp_path.parent)):
                if not enabled:
                    continue
                table = Path(kicad_directory) / filename
                before = capture(table)
                staged_table = stage / filename
                if before is not None:
                    staged_table.write_bytes(before)
                outcome = ensure_lib_entry(staged_table, kind, NICKNAME, target.as_posix(),
                                           'Company LCSC Manager imports', backup=False)
                if outcome == CONFLICT:
                    raise ValueError(f'KiCad already has a different {NICKNAME} library. Its registration was preserved.')
                put(table, staged_table.read_bytes())
            warnings.append('Restart KiCad to reload the registered libraries.')
        elif flat and (symbol or footprint):
            warnings.append('Downloaded files can be added to your KiCad symbol and footprint libraries.')
        elif symbol or footprint:
            warnings.append(f'Add {NICKNAME} in KiCad Manage Symbol/Footprint Libraries using {root}.')
        # Keep recoverable copies of every file this operation replaces.
        modified = {p: data for p, data in originals.items() if data is not None and p in changes and data != changes[p]}
        if modified:
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup = root if flat else root / '_backups' / stamp
            backup.mkdir(parents=True, exist_ok=True)
            for index, (path, data) in enumerate(modified.items()):
                name = f'{path.name}.{stamp}.{index}.bak' if flat else f'{index}_{path.name}'
                (backup / name).write_bytes(data)
        _commit(changes, originals)
    files = [str(path) for path in changes]
    if symbol and str(symbol_path) not in files:
        files.append(str(symbol_path))
    if footprint and str(fp_path) not in files:
        files.append(str(fp_path))
    if model:
        files.extend(str(path) for path in models.values() if str(path) not in files)
    return {'fields': fields, 'files': files, 'warnings': warnings,
            'models': {key: str(value) for key, value in models.items()}}
