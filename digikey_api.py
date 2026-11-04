import re
import time
import requests
from urllib.parse import quote
from api_manager import get_manager

# Basic helpers
def normalize_key(text):
    if text is None:
        return ""
    text = str(text).lower().replace("µ", "u").replace("μ", "u").replace("°", "")
    return re.sub(r"[^a-z0-9]+", "", text)


def clean_value(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("ValueText") or value.get("Value") or value.get("Name") or value.get("Text") or value.get("Status") or value.get("Description") or "")
    if isinstance(value, list):
        vals = []
        for item in value:
            v = clean_value(item)
            if v:
                vals.append(v)
        return ", ".join(vals)
    return str(value)


# API endpoints
DIGIKEY_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
DIGIKEY_KEYWORD_SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"
DIGIKEY_PRODUCT_DETAILS_URL = "https://api.digikey.com/products/v4/search/{product_number}/productdetails"

# client_id -> {"token": str, "expires_at": epoch seconds}
# DigiKey access tokens are valid for ~10 minutes. Requesting a fresh one for
# every part lookup wasted one HTTP round trip per part and made BOM runs far
# slower than they needed to be.
_TOKEN_CACHE = {}
_TOKEN_EXPIRY_MARGIN_SECONDS = 60


def invalidate_digikey_token(client_id):
    """Drop a cached token, e.g. after the API rejects it with 401."""
    _TOKEN_CACHE.pop(str(client_id or ""), None)


def get_digikey_token(client_id, client_secret, timeout=20, force_refresh=False, counter=None):
    if not client_id or not client_secret:
        raise ValueError('DigiKey CLIENT_ID or CLIENT_SECRET is missing.')
    cache_key = str(client_id)
    if not force_refresh:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time():
            return cached["token"]
    response = requests.post(
        DIGIKEY_TOKEN_URL,
        data={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
        timeout=timeout,
    )
    _count_request(counter)
    if response.status_code != 200:
        raise Exception(f"Could not obtain DigiKey token. Status: {response.status_code}\n{response.text}")
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise Exception('DigiKey token response does not contain access_token.')
    try:
        lifetime = int(payload.get("expires_in") or 600)
    except (TypeError, ValueError):
        lifetime = 600
    _TOKEN_CACHE[cache_key] = {
        "token": token,
        "expires_at": time.time() + max(lifetime - _TOKEN_EXPIRY_MARGIN_SECONDS, 0),
    }
    return token


def _count_request(counter, amount=1):
    """Track real HTTP calls so daily_limit accounting matches actual API usage."""
    if isinstance(counter, dict):
        counter["requests"] = int(counter.get("requests", 0)) + amount


def get_digikey_headers(token, client_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Client-Id": client_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _attempt_search_with_credentials(part_number, client_id, client_secret, entry_id=None, counter=None):
    """Low-level attempt using provided credentials. Returns product or raises on fatal error."""
    token = get_digikey_token(client_id, client_secret, counter=counter)
    headers = get_digikey_headers(token, client_id)
    response = requests.post(DIGIKEY_KEYWORD_SEARCH_URL, headers=headers, json={"Keywords": part_number, "Limit": 1, "Offset": 0}, timeout=30)
    _count_request(counter)
    if response.status_code == 401:
        # The cached token may simply have expired; retry once with a fresh one
        # before giving up and letting the caller rotate to another key.
        invalidate_digikey_token(client_id)
        token = get_digikey_token(client_id, client_secret, force_refresh=True, counter=counter)
        headers = get_digikey_headers(token, client_id)
        response = requests.post(DIGIKEY_KEYWORD_SEARCH_URL, headers=headers, json={"Keywords": part_number, "Limit": 1, "Offset": 0}, timeout=30)
        _count_request(counter)
    if response.status_code != 200:
        # propagate status for caller to decide rotation
        raise requests.HTTPError(f"Status: {response.status_code}\n{response.text}", response=response)
    products = response.json().get("Products", [])
    if not products:
        return None
    product = products[0]
    candidates = []
    for key in ["DigiKeyProductNumber", "ProductNumber", "ManufacturerProductNumber", "ManufacturerPartNumber"]:
        val = product.get(key)
        if val and val not in candidates:
            candidates.append(val)
    if part_number not in candidates:
        candidates.append(part_number)
    best = product
    for candidate in candidates:
        try:
            url = DIGIKEY_PRODUCT_DETAILS_URL.format(product_number=quote(str(candidate), safe=""))
            detail = requests.get(url, headers=headers, timeout=30)
            _count_request(counter)
            if detail.status_code == 200 and detail.json().get("Product"):
                best = detail.json()["Product"]
                break
        except requests.exceptions.RequestException:
            continue
    return best


def get_digikey_part_data(part_number, client_id=None, client_secret=None):
    """High-level search. If `client_id`/`client_secret` provided uses them, otherwise rotates through API-Keys.json via ApiKeyManager."""
    part_number = str(part_number or "").strip()
    if not part_number:
        raise ValueError('Enter a part number to search.')

    # If explicit credentials provided, use them directly and return result or raise.
    if client_id and client_secret:
        return _attempt_search_with_credentials(part_number, client_id, client_secret)

    # Resolved per call rather than bound at import time, so that editing keys in
    # Settings (which calls api_manager.reload_manager) takes effect immediately.
    mgr = get_manager()

    # Otherwise attempt rotation through available keys
    tried = set()
    max_attempts = max(1, len(mgr.keys))
    attempts = 0
    last_exception = None
    while attempts < max_attempts:
        attempts += 1
        entry, remaining = mgr.rotate_and_get(exclude=tried)
        if not entry:
            break
        entry_id = entry.get("id")
        if entry_id in tried:
            # mark usage to skip next time
            mgr.increment_usage(entry_id, 0)
            continue
        # Resolve credentials from entry
        cid = entry.get("client_id")
        csecret = entry.get("client_secret")
        if not cid or not csecret:
            key = str(entry.get("key") or "")
            if ":" in key:
                cid, csecret = key.split(":", 1)
        if not cid or not csecret:
            # unusable entry — mark exhausted and continue
            mgr.blocked_keys.add(entry_id)
            tried.add(entry_id)
            continue
        # A single "search" is really a token call plus a keyword call plus up to a
        # few productdetails calls. Counting it as 1 made daily_limit undercount
        # real consumption several-fold, so rotation kicked in far too late.
        counter = {"requests": 0}
        try:
            product = _attempt_search_with_credentials(part_number, cid, csecret, entry_id=entry_id, counter=counter)
            mgr.increment_usage(entry_id, counter["requests"])
            return product
        except requests.HTTPError as he:
            mgr.increment_usage(entry_id, counter["requests"])
            resp = getattr(he, 'response', None)
            status = getattr(resp, 'status_code', None)
            # If rate limited or unauthorized, mark this key as used up and try next
            if status in (401, 403, 429):
                invalidate_digikey_token(cid)
                mgr.blocked_keys.add(entry_id)
                tried.add(entry_id)
                last_exception = he
                continue
            # other HTTP errors — propagate
            last_exception = he
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            # DigiKey itself is unreachable: another key cannot help, and each
            # attempt would cost a further connect/read timeout. Surface it so
            # callers (the BOM job) can stop instead of timing out per key.
            mgr.increment_usage(entry_id, counter["requests"])
            raise
        except Exception as e:
            # other error — try next key
            mgr.increment_usage(entry_id, counter["requests"])
            tried.add(entry_id)
            last_exception = e
            continue

    # If we reach here no key returned success
    if last_exception:
        raise last_exception
    raise ValueError('No usable DigiKey credentials found in API-Keys.json, or daily limits have been reached.')


# -----------------------------
# Walk and extract product fields
# -----------------------------
def add_result(result, key, value):
    value = clean_value(value)
    if value:
        result[key] = value
        result[key.replace(" ", "_")] = value
        result[normalize_key(key)] = value


def get_from_result(result, *keys):
    for key in keys:
        if key in result and result[key] not in [None, ""]:
            return result[key]
        nkey = normalize_key(key)
        if nkey in result and result[nkey] not in [None, ""]:
            return result[nkey]
    return ""


def walk_digikey_object(obj, result):
    if isinstance(obj, dict):
        name = obj.get("ParameterText") or obj.get("Parameter") or obj.get("ParameterName") or obj.get("AttributeName") or obj.get("Name") or obj.get("Label") or obj.get("Title")
        value = obj.get("ValueText") or obj.get("ParameterValue") or obj.get("AttributeValue") or obj.get("Value") or obj.get("Text") or obj.get("Status")
        if name and value:
            add_result(result, name, value)
        for key, value in obj.items():
            if isinstance(value, (str, int, float, bool)):
                add_result(result, key, value)
            else:
                walk_digikey_object(value, result)
    elif isinstance(obj, list):
        for item in obj:
            walk_digikey_object(item, result)


def add_common_aliases(result):
    alias_map = {
        "Operating_Temperature": ["Operating Temperature", "Operating Temperature Range", "Temperature - Operating", "Temperature Range", "Temperature"],
        "Temperature_Coefficient": ["Temperature Coefficient", "Temperature Coefficient (TCR)", "TCR", "TC", "ppm/°C", "PPM/C", "ppm/C"],
        "Mounting_Type": ["Mounting Type", "Mounting", "Termination", "Termination Style"],
        "Package": ["Package / Case", "Supplier Device Package", "Package", "Case", "Size / Dimension"],
        "Tolerance": ["Tolerance", "Resistance Tolerance", "Capacitance Tolerance", "Inductance Tolerance"],
        "Voltage": ["Voltage", "Voltage Rating", "Voltage - Rated", "Rated Voltage"],
        # Generic ratings first, then the diode/TVS-specific names. These used to be
        # two separate "Current"/"Power" entries in this literal, so the generic
        # aliases (notably "Current Rating") were silently dropped by Python.
        "Current": [
            "Current", "Current Rating", "Current Rating (Max)", "Current - Rated",
            "Average Rectified Current", "Drain Current",
            "Current - Peak Pulse (10/1000µs)", "Current - Peak Pulse", "Peak Pulse Current",
            "Peak Pulse Current (Ipp)", "Current - Average Rectified (Io)",
            "Current - Reverse Leakage @ Vr", "Reverse Leakage Current", "Ipp", "If", "IF",
        ],
        "Power": [
            "Power", "Power Rating", "Power Rating (Watts)", "Power (Watts)", "Power (W)", "Wattage",
            "Power - Peak Pulse", "Peak Pulse Power", "Power Dissipation", "Power - Max",
        ],
        "Resistance": ["Resistance", "Resistance @ 25°C", "Resistance Ohms", "Resistance Value", "Value"],
        "Frequency": ["Frequency", "Frequency Stability", "Frequency Tolerance"],
        "Load_Capacitance": ["Load Capacitance", "Capacitance"],
        "Pins": ["Number of Positions", "Number of Pins", "Pins", "Contacts", "Number of Contacts"],
        "Reach": ["REACH Status", "Reach", "REACH", "Reach Status"],
        "Rohs": ["RoHS Status", "Rohs", "RoHS", "RoHS Compliant"],
        "Forward_Voltage": ["Voltage - Forward (Vf) (Max) @ If", "Forward Voltage", "Forward Voltage (Vf)", "Vf", "VF"],
        "Reverse_Voltage": ["Voltage - DC Reverse (Vr) (Max)", "Voltage - Reverse Standoff (Typ)", "Reverse Voltage", "Reverse Standoff Voltage", "Working Peak Reverse Voltage", "Reverse Stand-Off Voltage", "Vr", "VR", "VRWM"],
        "Breakdown_Voltage": ["Voltage - Breakdown (Min)", "Breakdown Voltage", "Reverse Breakdown Voltage", "Vbr", "VBR", "V(BR)"],
        "Clamping_Voltage": ["Voltage - Clamping (Max) @ Ipp", "Clamping Voltage", "Maximum Clamping Voltage", "Vc", "VC"],
        "Type": ["Type", "Technology", "Diode Type", "Product Type"]
    }
    for target, keys in alias_map.items():
        add_result(result, target, get_from_result(result, *keys))


def extract_digikey_fields(product):
    result = {}
    if not product:
        return result
    walk_digikey_object(product, result)
    desc = product.get("Description", {}) or {}
    manufacturer = product.get("Manufacturer", {}) or {}
    category = product.get("Category", {}) or {}
    manufacturer_name = clean_value(manufacturer.get("Name"))
    mpn = clean_value(product.get("ManufacturerProductNumber") or product.get("ManufacturerPartNumber") or product.get("Manufacturer Part Number"))
    dkpn = clean_value(product.get("DigiKeyProductNumber") or product.get("ProductNumber") or product.get("DigiKey Part Number"))
    product_desc = clean_value(desc.get("ProductDescription") or product.get("ProductDescription"))
    detailed_desc = clean_value(desc.get("DetailedDescription") or product.get("DetailedDescription"))
    category_name = clean_value(category.get("Name") or product.get("CategoryName"))
    for key, value in [
        ("Manufacturer", manufacturer_name),
        ("Mfr", manufacturer_name),
        ("Manufacturer_Part_Number", mpn),
        ("Manufacturer Part Number", mpn),
        ("Mfr Part Number", mpn),
        ("MPN", mpn),
        ("Part Number", mpn),
        ("DigiKey_Part_Number", dkpn),
        ("DigiKey Part Number", dkpn),
        ("DKPN", dkpn),
        ("Description", product_desc or detailed_desc),
        ("Detailed_Description", detailed_desc),
        ("Category", category_name),
        ("Datasheet", product.get("DatasheetUrl") or product.get("PrimaryDatasheet") or product.get("Datasheet")),
        ("Quantity_Available", product.get("QuantityAvailable")),
        ("Stock", product.get("QuantityAvailable")),
    ]:
        add_result(result, key, value)
    packaging = product.get("Packaging")
    add_result(result, "Packaging", packaging.get("Name") if isinstance(packaging, dict) else packaging)
    status = product.get("ProductStatus")
    if isinstance(status, dict):
        status_value = status.get("Status") or status.get("Name") or status.get("Value") or status.get("Text")
    else:
        status_value = status
    for key in ["Status", "Part Status", "Product Status", "ProductStatus"]:
        add_result(result, key, status_value)
    add_common_aliases(result)
    return result


def get_part_status_from_digikey(product, extracted=None):
    if extracted is None:
        extracted = extract_digikey_fields(product)
    return clean_value(get_from_result(extracted, "Part Status", "Product Status", "ProductStatus", "Status", "Lifecycle Status", "Lifecycle")).strip()


# -----------------------------
# Price/stock helpers
# -----------------------------
def _normalize_number(number):
    return str(number).replace(",", ".").strip()


def packaging_text_candidates(obj):
    values = []
    if isinstance(obj, dict):
        for key in ["PackageType", "Packaging", "DigiKeyProductNumber", "ProductNumber", "Name", "Type", "Description"]:
            value = obj.get(key)
            values.append(clean_value(value) if isinstance(value, dict) else str(value or ""))
        for value in obj.values():
            if isinstance(value, dict):
                name = value.get("Name") or value.get("Text") or value.get("Value")
                if name:
                    values.append(str(name))
    return " ".join(values).lower()


# "ct" must never be matched as a bare substring: ordinary words such as
# "Indu(ct)or", "Conne(ct)or" and "produ(ct)" contain it and would make every
# variation look like Cut Tape, silently disabling the preference below.
CUT_TAPE_PHRASES = ("cut tape", "digi-reel", "digireel", "digi reel")

# Matches the standalone token "ct" and DigiKey's Cut-Tape part-number suffix
# (e.g. "311-10.0KCRCT-ND"), but not "ct" embedded inside a longer word.
CUT_TAPE_TOKEN_RE = re.compile(r"(?:\bct\b|ct-nd\b)")


def is_cut_tape_or_digireel_text(text):
    text = str(text).lower()
    if any(phrase in text for phrase in CUT_TAPE_PHRASES):
        return True
    return bool(CUT_TAPE_TOKEN_RE.search(text))


def collect_pricing_from_object(obj, preferred_only=False):
    result = []
    if not isinstance(obj, dict):
        return result
    if preferred_only and not is_cut_tape_or_digireel_text(packaging_text_candidates(obj)):
        return result
    pricing = obj.get("StandardPricing")
    items = pricing if isinstance(pricing, list) else [pricing] if isinstance(pricing, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = item.get("BreakQuantity") or item.get("Quantity") or item.get("MinimumQuantity") or 1
        price = item.get("UnitPrice")
        try:
            qty = int(float(str(qty)))
        except Exception:
            qty = 1
        if price not in [None, ""]:
            result.append({"break_qty": qty, "unit_price": price})
    return result


def get_digikey_pricing_candidates(product, preferred_only=True):
    if not product:
        return []
    result = []
    for variation in product.get("ProductVariations") or []:
        result.extend(collect_pricing_from_object(variation, preferred_only=preferred_only))
    result.extend(collect_pricing_from_object(product, preferred_only=preferred_only))
    if preferred_only and not result:
        return get_digikey_pricing_candidates(product, preferred_only=False)
    return sorted(result, key=lambda item: item["break_qty"])


def get_digikey_price(product):
    candidates = get_digikey_pricing_candidates(product, preferred_only=True)
    return clean_value(candidates[0]["unit_price"]) if candidates else ""


def get_digikey_price_for_quantity(product, quantity):
    candidates = get_digikey_pricing_candidates(product, preferred_only=True)
    if not candidates:
        return ""
    selected = ""
    for item in candidates:
        if item["break_qty"] <= quantity:
            selected = item["unit_price"]
        else:
            break
    if selected == "":
        selected = candidates[0]["unit_price"]
    return clean_value(selected)


def digikey_stock_source_contains_excluded_flag(obj):
    excluded_tokens = [
        "marketplace",
        "marketplace product",
        "marketplace products",
        "tariffed",
        "tariffed product",
        "tariffed products",
    ]

    true_values = ["true", "yes", "1", "y"]

    def walk(value):
        if value is None:
            return False
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).lower()
                if any(token in key_text for token in excluded_tokens):
                    if isinstance(item, bool):
                        if item:
                            return True
                    elif str(item).strip().lower() in true_values:
                        return True
                if walk(item):
                    return True
            return False
        if isinstance(value, list):
            return any(walk(item) for item in value)
        text = str(value).strip().lower()
        return text in excluded_tokens
    return walk(obj)


def get_quantity_from_digikey_stock_source(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ["QuantityAvailable", "QuantityAvailableForPackageType", "QuantityAvailableforPackageType", "QuantityOnHand", "AvailableQuantity", "InStock"]:
        qty = obj.get(key)
        if qty not in [None, ""]:
            return clean_value(qty)
    return ""


def get_digikey_stock(product):
    if not product:
        return ""
    variations = product.get("ProductVariations") or []
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        if digikey_stock_source_contains_excluded_flag(variation):
            continue
        if is_cut_tape_or_digireel_text(packaging_text_candidates(variation)):
            qty = get_quantity_from_digikey_stock_source(variation)
            if qty not in [None, ""]:
                return clean_value(qty)
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        if digikey_stock_source_contains_excluded_flag(variation):
            continue
        qty = get_quantity_from_digikey_stock_source(variation)
        if qty not in [None, ""]:
            return clean_value(qty)
    if not digikey_stock_source_contains_excluded_flag(product):
        qty = get_quantity_from_digikey_stock_source(product)
        if qty not in [None, ""]:
            return clean_value(qty)
    return ""
