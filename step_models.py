"""Identify STEP application protocols and rank actual available model files."""
from pathlib import Path
import re

STEP_PRIORITY = {'AP242': 0, 'AP214': 1, 'AP203': 2, 'Unknown': 3}


def step_protocol(data):
    header = data[:131072].decode('ascii', errors='ignore')
    header = re.sub(r'/\*.*?\*/', '', header, flags=re.S).upper()
    header = header.split('ENDSEC;', 1)[0]
    schemas = re.search(r'FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)\s*;', header, re.S)
    if not schemas:
        return 'Unknown'
    names = schemas.group(1)
    if re.search(r'\bAP242\b|AP242_', names):
        return 'AP242'
    if 'AUTOMOTIVE_DESIGN' in names or re.search(r'\bAP214\b|AP214_', names):
        return 'AP214'
    if 'CONFIG_CONTROL_DESIGN' in names or re.search(r'\bAP203\b|AP203_', names):
        return 'AP203'
    return 'Unknown'


def file_protocol(path):
    try:
        with Path(path).open('rb') as handle:
            return step_protocol(handle.read(131072))
    except OSError:
        return 'Unknown'


def rank_step_files(paths):
    """Stable ordering: preserve caller preference among files of the same protocol."""
    return sorted(paths, key=lambda p: STEP_PRIORITY[file_protocol(p)])


def local_step_files(folder, code):
    if not folder or not Path(folder).is_dir():
        return []
    pattern = re.compile(rf'{re.escape(code)}(?:[_. -]AP(?:242|214|203))?\.(?:step|stp)', re.I)
    candidates = [p for p in Path(folder).iterdir() if p.is_file() and pattern.fullmatch(p.name)]
    candidates.sort(key=lambda p: (p.name.casefold() != f'{code}.step'.casefold(), p.name.casefold()))
    return rank_step_files(candidates)
