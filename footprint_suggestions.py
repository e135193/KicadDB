"""Footprint suggestions: installed KiCad library first, web research second.

Order of work in `suggest()`:

1. Local: the Description and Package fields of the record form are matched
   against the installed KiCad footprint libraries. A chip size code such as
   0402 / 0603 / 0805 / 1206 together with the component table (Resistor,
   Capacitor, ...) resolves directly to e.g. `Resistor_SMD:R_0603_1608Metric`;
   package families such as SOT-23-5, SOIC-8 or LQFP-48 7x7mm resolve through
   the same pin/dimension/pitch matching used for web results. No network.
2. Web: only when the local step finds nothing and an MPN is known. Google
   Programmable Search is used when `google_api_key` and `google_cse_id` are
   set in settings.json; otherwise DuckDuckGo, then Bing. Several query
   variants are tried until one yields package information for the exact MPN.
3. Supplier: when the web step still produced no package data, the configured
   DigiKey credentials are used once.

Candidates are never applied automatically; the user picks one in the window.
"""
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote_plus, urlparse

import requests
import config


# ---------------------------------------------------------------------------
# Installed libraries
# ---------------------------------------------------------------------------

def _kicad_version_key(path):
    match = re.search(r"(\d+)(?:\.(\d+))?", path.parent.parent.parent.name if len(path.parents) > 2 else "")
    return tuple(int(g or 0) for g in match.groups()) if match else (0, 0)


def library_roots(extra=None):
    """Folders scanned for `*.pretty` libraries, most specific first.

    Order: an explicitly chosen folder, `footprint_library_dir` from
    settings.json, the company's own library/footprints.pretty next to the exe or in
    the KiCad library root (`config.LIBRARY_ROOT`, found by walking up from
    the app folder), KiCad environment variables, then the standard KiCad
    installation folders (newest version first).
    """
    candidates = [extra, config._setting("footprint_library_dir"),
                  Path(config.FOOTPRINT_LIBRARY_DIR)]
    candidates += [v for k, v in os.environ.items() if re.fullmatch(r"KICAD\d*_FOOTPRINT_DIR", k)]
    appdata = Path(os.environ.get("APPDATA", str(Path.home()))) / "kicad"
    if appdata.is_dir():
        for settings in sorted(appdata.glob("*/kicad_common.json"), reverse=True):
            try:
                data = json.loads(settings.read_text(encoding="utf-8"))
                candidates += [v for k, v in data.get("environment", {}).get("vars", {}).items()
                               if re.fullmatch(r"KICAD\d*_FOOTPRINT_DIR", k)]
            except (OSError, ValueError, AttributeError):
                continue
    installs = []
    for base in [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "KiCad",
                 Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "KiCad",
                 # Per-user installer (KiCad 8+ offers this by default).
                 Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Programs" / "KiCad"]:
        if base.is_dir():
            installs += list(base.glob("*/share/kicad/footprints"))
    candidates += sorted(installs, key=_kicad_version_key, reverse=True)
    candidates += ["/usr/share/kicad/footprints", "/usr/local/share/kicad/footprints",
                   "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"]
    roots = []
    for value in candidates:
        if value:
            path = Path(os.path.expandvars(str(value))).expanduser()
            if path.is_dir() and path not in roots:
                roots.append(path)
    return roots


def library_nickname(library):
    """Name KiCad uses for a `.pretty` folder.

    Standard libraries are registered under their folder name. The company
    library folder is `footprints.pretty`, but fp-lib-table and every existing
    record call it `KicadDB`, so a suggestion must use that nickname or the
    footprint would not resolve in KiCad.
    """
    library = Path(library)
    if library.name.lower() == config.COMPANY_FOOTPRINT_LIBRARY_DIR_NAME.lower():
        return config.COMPANY_FOOTPRINT_LIBRARY_NICKNAME
    return library.stem


def installed_footprints(roots):
    found = {}
    for root in roots:
        libraries = [root] if root.suffix == ".pretty" else sorted(root.glob("*.pretty"))
        for library in libraries:
            for path in library.glob("*.kicad_mod"):
                found.setdefault(f"{library_nickname(library)}:{path.stem}", str(path))
    return found


# ---------------------------------------------------------------------------
# Package text analysis
# ---------------------------------------------------------------------------

FAMILY = r"LQFP|TQFP|HTQFP|VQFN|WQFN|UQFN|QFN|TSDSON|TDSON|TDFN|DFN|TSSOP|HTSSOP|SSOP|MSOP|SOICW|SOIC|SOP|TSOP|SOT|SOD|TO|DIP|PDIP|LGA|BGA|WLCSP"

# Imperial chip size code -> metric code used in KiCad footprint names.
SIZE_CODES = {"01005": "0402", "0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012",
              "1206": "3216", "1210": "3225", "1812": "4532", "2010": "5025", "2512": "6332"}

# Component table -> (KiCad library, footprint name prefix) for chip footprints.
CATEGORY_PREFIXES = {
    "resistor": [("Resistor_SMD", "R")],
    "ptcntc": [("Resistor_SMD", "R")],
    "capacitor": [("Capacitor_SMD", "C")],
    "inductor": [("Inductor_SMD", "L")],
    "diode": [("Diode_SMD", "D"), ("LED_SMD", "LED")],
    "opto": [("LED_SMD", "LED"), ("Diode_SMD", "D")],
    "fuse": [("Fuse", "Fuse")],
}


def _category(table_name):
    return re.sub(r"[^a-z0-9]", "", str(table_name or "").lower())


def size_codes(text):
    """Imperial chip size codes found in free text, as (imperial, metric) pairs.

    A bare metric code is ambiguous ("1608" could be anything); it counts only
    with an explicit "Metric" marker. An imperial code directly followed by
    "Metric" is a metric code of another size and is ignored.
    """
    found = []
    upper = str(text or "").upper()
    for imperial, metric in SIZE_CODES.items():
        explicit_metric = re.search(rf"(?<![0-9A-Z]){metric}\s*(?:METRIC|METRİK)\b", upper)
        imperial_found = re.search(rf"(?<![0-9A-Z.]){imperial}(?![0-9A-Z])(?!\s*(?:METRIC|METRİK))", upper)
        if explicit_metric or imperial_found:
            found.append((imperial, metric))
    return found


def package_specs(value):
    value = str(value).upper().replace("×", "X")
    specs = []
    for match in re.finditer(rf"(?<![A-Z0-9])({FAMILY})[- _]?(\d+)(?!\d)", value):
        specs.append((match[1], int(match[2])))
    for match in re.finditer(rf"(?<!\d)(\d+)[- ]+({FAMILY})(?![A-Z])", value):
        specs.append((match[2], int(match[1])))
    dimensions = re.search(r"(\d+(?:\.\d+)?)\s*X\s*(\d+(?:\.\d+)?)\s*MM", value)
    pitch = re.search(r"(?:(?<![A-Z0-9])P|PITCH\s*[:=]?\s*)(\d+(?:\.\d+)?)\s*MM", value)
    return list(dict.fromkeys(specs)), tuple(sorted(map(float, dimensions.groups()))) if dimensions else None, float(pitch[1]) if pitch else None


def has_package_information(text):
    return bool(package_specs(text)[0] or size_codes(text))


# ---------------------------------------------------------------------------
# Web research
# ---------------------------------------------------------------------------

class SearchResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self.current = {"title": "", "snippet": "", "url": attrs.get("href", "")}
            self.results.append(self.current)
            self.capture = "title"
        elif "result__snippet" in classes and self.current:
            self.capture = "snippet"

    def handle_endtag(self, tag):
        if tag == "a":
            self.capture = None

    def handle_data(self, data):
        if self.current and self.capture:
            self.current[self.capture] += data


def search_queries(mpn, manufacturer=""):
    """Query variants, most specific first; the first one with usable evidence wins."""
    mpn = str(mpn).strip()
    manufacturer = " ".join(str(manufacturer or "").split())
    queries = []
    if manufacturer:
        queries.append(f'"{mpn}" {manufacturer} package')
    queries += [f'"{mpn}" package footprint pin pitch',
                f'"{mpn}" datasheet package case',
                f'"{mpn}" footprint']
    return list(dict.fromkeys(queries))


def google_settings():
    key = str(config._setting("google_api_key") or "").strip()
    cse = str(config._setting("google_cse_id") or "").strip()
    return (key, cse) if key and cse else None


def _search_google(query, credentials):
    key, cse = credentials
    response = requests.get("https://www.googleapis.com/customsearch/v1",
                            params={"key": key, "cx": cse, "q": query, "num": 10}, timeout=(5, 12))
    response.raise_for_status()
    return [{"title": item.get("title", ""), "snippet": item.get("snippet", ""), "url": item.get("link", "")}
            for item in response.json().get("items", [])]


def _search_duckduckgo(query):
    response = requests.get("https://html.duckduckgo.com/html/", params={"q": query},
                            headers={"User-Agent": "Mozilla/5.0 CompanyKiCadDB/1.0"}, timeout=(5, 12))
    response.raise_for_status()
    parser = SearchResults()
    parser.feed(response.text[:1_500_000])
    return parser.results


def _search_bing(query):
    response = requests.get("https://www.bing.com/search", params={"q": query, "format": "rss"}, timeout=(5, 12))
    response.raise_for_status()
    try:
        feed = ET.fromstring(response.text[:1_500_000])
    except ET.ParseError:
        return []
    return [{"title": item.findtext("title", ""), "snippet": item.findtext("description", ""),
             "url": item.findtext("link", "")} for item in feed.findall("./channel/item")]


def _search(query):
    """One query through the configured engines; raises only if every engine failed to connect."""
    credentials = google_settings()
    engines = ([lambda q: _search_google(q, credentials)] if credentials else []) + [_search_duckduckgo, _search_bing]
    failures = []
    for engine in engines:
        try:
            results = engine(query)
        except requests.RequestException as exc:
            failures.append(exc)
            continue
        except ValueError:
            # Unexpected (non-JSON) answer from the API: treat as no results.
            continue
        if results:
            return results
    if failures and len(failures) == len(engines):
        raise failures[-1]
    return []


def _evidence_from_results(results, mpn):
    evidence = []
    exact = re.compile(r"(?<![A-Za-z0-9])" + re.escape(mpn) + r"(?![A-Za-z0-9])", re.I)
    for result in results[:10]:
        content = html.unescape(result["title"] + " " + result["snippet"])
        if not exact.search(content):
            continue
        url = result["url"]
        if url.startswith("//"):
            url = "https:" + url
        redirected = parse_qs(urlparse(url).query).get("uddg")
        if redirected:
            url = redirected[0]
        if urlparse(url).scheme not in ("http", "https"):
            continue
        evidence.append({"text": content, "source": url, "verified": False, "kind": "web"})
    return evidence


def online_evidence(mpn, manufacturer="", report=lambda *a: None, cancelled=lambda: False):
    """Public MPN search; snippets remain explicitly unverified evidence.

    Query variants are tried in order and the search stops as soon as one of
    them yields package information for the exact MPN, so a good first hit
    costs a single request.
    """
    evidence = []
    seen = set()
    for index, query in enumerate(search_queries(mpn, manufacturer), 1):
        if cancelled():
            break
        report(f"Searching the web for the MPN ({index})...")
        for item in _evidence_from_results(_search(query), mpn):
            if item["source"] not in seen:
                seen.add(item["source"])
                evidence.append(item)
        if any(has_package_information(item["text"]) for item in evidence):
            break
    return evidence


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

LEVELS = {"form": 'Record form', "web": 'Verification required', "supplier": 'Package data'}
SCORES = {"form": 40, "web": 30, "supplier": 50}


def _chip_matches(footprints, text, category, item, result):
    """Chip-size matches (0603 etc.) for one evidence item; adds records to `result`."""
    codes = size_codes(text)
    if not codes:
        return
    prefixes = CATEGORY_PREFIXES.get(category)
    mentions_led = re.search(r"\bLED\b", text, re.I)
    for imperial, metric in codes:
        if prefixes:
            for order, (library, prefix) in enumerate(prefixes):
                base = f"{library}:{prefix}_{imperial}_{metric}Metric"
                if prefix in ("LED", "D"):
                    # The Diode table holds both diodes and LEDs: the text decides.
                    preferred = (prefix == "LED") == bool(mentions_led)
                else:
                    preferred = order == 0
                for identifier, path in footprints.items():
                    if identifier != base and not identifier.startswith(base + "_"):
                        continue
                    score = SCORES[item["kind"]] + (25 if identifier == base else 15) - (0 if preferred else 15)
                    reason = 'Body size code and component type match'
                    if identifier != base:
                        reason += ' (variant)'
                    result.append({"name": identifier, "path": path, "reason": reason, "score": score,
                                   "source": item["source"], "evidence": item["text"],
                                   "level": LEVELS[item["kind"]]})
        else:
            pattern = re.compile(rf"^[^:]+:[A-Za-z]+_{imperial}_{metric}Metric(?:_|$)")
            for identifier, path in footprints.items():
                if pattern.match(identifier):
                    result.append({"name": identifier, "path": path, "score": SCORES[item["kind"]],
                                   "reason": 'Body size code matches (component type unknown)',
                                   "source": item["source"], "evidence": item["text"],
                                   "level": LEVELS[item["kind"]]})


def rank_candidates(footprints, evidence, table_name):
    """Score installed footprints against every evidence item; best record per footprint."""
    category = _category(table_name)
    records = []
    for item in evidence:
        item.setdefault("kind", "supplier" if item.get("verified") else "web")
        _chip_matches(footprints, item["text"], category, item, records)
        specs, dimensions, pitch = package_specs(item["text"])
        if not specs:
            continue
        for identifier, path in footprints.items():
            name = identifier.split(":", 1)[1]
            candidate_specs, candidate_dimensions, candidate_pitch = package_specs(name)
            matches = set(specs).intersection(candidate_specs)
            if not matches:
                continue
            # SOT-23-5 must never be treated as a three-pad SOT-23.
            consistent = True
            for family, number in matches:
                if family in ("SOT", "SOD", "TO"):
                    pattern = rf"{family}[- _]?{number}[- _](\d+)(?!\d)"
                    a = re.search(pattern, item["text"], re.I)
                    b = re.search(pattern, name, re.I)
                    if (a.group(1) if a else None) != (b.group(1) if b else None):
                        consistent = False
            if not consistent:
                continue
            if dimensions and candidate_dimensions and dimensions != candidate_dimensions:
                continue
            if pitch and candidate_pitch and abs(pitch - candidate_pitch) > 0.001:
                continue
            reason = 'Package family/number matches'
            score = SCORES[item["kind"]]
            if dimensions and candidate_dimensions:
                score += 15
                reason += '; body dimensions match'
            if pitch and candidate_pitch:
                score += 15
                reason += '; pin pitch matches'
            records.append({"name": identifier, "path": path, "reason": reason, "score": score,
                            "source": item["source"], "evidence": item["text"],
                            "level": LEVELS[item["kind"]]})
    best = {}
    for record in records:
        upper = record["name"].upper()
        if "HAND" in upper:
            record["score"] -= 5
        # Exposed-pad and thermal-via variants only when the evidence asks for them.
        if "THERMALVIAS" in upper:
            record["score"] -= 3
        if re.search(r"_\d+EP\b|_EP\d", upper) and not re.search(r"\bEP\b|EXPOSED|THERMAL PAD", record["evidence"], re.I):
            record["score"] -= 2
        current = best.get(record["name"])
        if current is None or record["score"] > current["score"]:
            best[record["name"]] = record
    # Ties: the plain footprint name before longer variants.
    return sorted(best.values(), key=lambda x: (-x["score"], len(x["name"]), x["name"]))[:80]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def form_evidence(package, description):
    evidence = []
    if str(package or "").strip():
        evidence.append({"text": str(package).strip(), "source": 'Package field in the record form',
                         "verified": False, "kind": "form"})
    if str(description or "").strip():
        evidence.append({"text": str(description).strip(), "source": 'Description field in the record form',
                         "verified": False, "kind": "form"})
    return evidence


def suggest(mpn, package, table_name, extra_root=None, report=lambda *a: None, cancelled=lambda: False,
            description="", manufacturer="", current_footprint=""):
    """Local library first; web and supplier research only when it finds nothing."""
    messages = []
    report('Scanning installed KiCad footprints...')
    roots = library_roots(extra_root)
    footprints = installed_footprints(roots) if not cancelled() else {}
    evidence = form_evidence(package, description)
    candidates = rank_candidates(footprints, evidence, table_name) if footprints else []
    current = str(current_footprint or '').strip()
    if current and current.casefold() not in ('none', 'null', '-'):
        # A saved assignment need not resemble a package name. Resolve it by
        # library ID; never silently replace a missing ID with another library.
        saved = [name for name in footprints if name == current or
                 (':' not in current and name.split(':', 1)[-1] == current)]
        if len(saved) == 1:
            name = saved[0]
            candidates = [c for c in candidates if c['name'] != name]
            candidates.insert(0, {'name': name, 'path': footprints[name], 'score': 100,
                                 'level': 'Current assignment', 'reason': 'Footprint saved in the record form',
                                 'source': 'Footprint field in the record form', 'evidence': current})
            messages.append('Showing the current footprint from the record form.')
        else:
            messages.append(f'Saved footprint not found uniquely in the available libraries: {current}. '
                            'Select its library folder or choose a suggestion below.')
    if candidates:
        messages.append('Matched from the installed KiCad library using the Description/Package fields; '
                        'no web search was needed.')
        return {"candidates": candidates, "messages": messages, "roots": [str(r) for r in roots],
                "evidence": evidence, "local": True}
    if not roots:
        messages.append('KiCad footprint folder not found. Select a library folder.')
    if cancelled():
        return {"candidates": [], "messages": ['Cancelled.'], "roots": [str(r) for r in roots],
                "evidence": evidence, "local": False}
    if mpn:
        try:
            web = online_evidence(mpn, manufacturer, report, cancelled)
            evidence.extend(web)
            messages.append(f"Web search: {len(web)} sources matching the MPN.")
        except requests.RequestException:
            messages.append('Web search unavailable; using existing package information.')
    elif not evidence:
        messages.append('Enter a manufacturer part number, package or description to get suggestions.')
    if cancelled():
        return {"candidates": [], "messages": ['Cancelled.'], "roots": [str(r) for r in roots],
                "evidence": evidence, "local": False}
    # Reuse configured supplier credentials only when public search supplied no package.
    if mpn and not any(has_package_information(e["text"]) for e in evidence):
        try:
            from digikey_api import get_digikey_part_data, extract_digikey_fields, get_from_result
            report('Checking DigiKey package information...')
            product = get_digikey_part_data(mpn)
            exact_mpn = str((product or {}).get("ManufacturerProductNumber") or (product or {}).get("ManufacturerPartNumber") or "")
            if exact_mpn.strip().casefold() == mpn.strip().casefold():
                fields = extract_digikey_fields(product)
                parts = [get_from_result(fields, key) for key in ("Package / Case", "Supplier Device Package", "Package")]
                evidence.append({"text": " ".join(str(p) for p in parts if p), "source": product.get("ProductUrl") or
                                 "https://www.digikey.com/en/products/result?keywords=" + quote_plus(mpn),
                                 "verified": True, "kind": "supplier"})
        except Exception:
            messages.append('DigiKey package information unavailable or API not configured.')
    candidates = rank_candidates(footprints, evidence, table_name) if footprints else []
    if roots and not candidates:
        messages.append('No installed footprints match the package information.')
    return {"candidates": candidates, "messages": messages, "roots": [str(r) for r in roots],
            "evidence": evidence, "local": False}
