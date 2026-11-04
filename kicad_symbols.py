import os
import re

KICAD_SYMBOL_CACHE = {}


def unescape_kicad_string(text):
    if text is None:
        return ""
    text = str(text)
    text = text.replace('\\"', '"')
    text = text.replace('\\\\', '\\')
    return text


def format_kicad_symbol_name_for_library(file_path, symbol_name):
    library_name = os.path.splitext(os.path.basename(str(file_path or "")))[0]
    symbol_name = unescape_kicad_string(symbol_name).strip()
    if not library_name or not symbol_name:
        return symbol_name
    expected_prefix = library_name + ":"
    if symbol_name.startswith(expected_prefix):
        return symbol_name
    if ":" in symbol_name:
        symbol_name = symbol_name.split(":")[-1].strip()
    return expected_prefix + symbol_name


def extract_kicad_symbols_from_file(file_path):
    if not os.path.isfile(file_path):
        return []
    cache_key = os.path.abspath(file_path)
    # Cache per (path, mtime): a .kicad_sym edited in KiCad while the app is
    # open used to keep serving the stale symbol list until restart.
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = None
    cached = KICAD_SYMBOL_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='latin-1') as f:
            content = f.read()
    symbols = []
    depth = 0
    i = 0
    length = len(content)
    while i < length:
        ch = content[i]
        if ch == "(":
            if depth == 1 and content.startswith("(symbol", i):
                j = i + len("(symbol")
                while j < length and content[j].isspace():
                    j += 1
                if j < length and content[j] == '"':
                    j += 1
                    name_chars = []
                    escaped = False
                    while j < length:
                        c = content[j]
                        if escaped:
                            name_chars.append("\\" + c)
                            escaped = False
                        elif c == "\\":
                            escaped = True
                        elif c == '"':
                            break
                        else:
                            name_chars.append(c)
                        j += 1
                    raw_symbol_name = unescape_kicad_string("".join(name_chars)).strip()
                    if raw_symbol_name:
                        if not re.search(r"_\d+_\d+$", raw_symbol_name):
                            formatted_symbol_name = format_kicad_symbol_name_for_library(file_path, raw_symbol_name)
                            if formatted_symbol_name and formatted_symbol_name not in symbols:
                                symbols.append(formatted_symbol_name)
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        i += 1
    symbols = sorted(symbols, key=lambda x: x.lower())
    KICAD_SYMBOL_CACHE[cache_key] = (mtime, symbols)
    return symbols
