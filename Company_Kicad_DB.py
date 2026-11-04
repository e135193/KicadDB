
import os
import re
import sys
import json
import queue
import logging
import threading
import traceback
import base64
import sqlite3
import logging.handlers
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pandas as pd
import requests
import webbrowser
import digikey_api
# Local stock, BOM stock, and Purchasing features removed

# BOM jobs stop querying a supplier after this many consecutive connection
# failures (circuit breaker); see App._run_bom_job.
SUPPLIER_FAILURE_LIMIT = 3

# These helpers are shared with digikey_api and used to be copy-pasted here as
# well. Importing them keeps a single definition, so a fix lands in one place.
from digikey_api import (
    normalize_key, clean_value, _normalize_number,
    add_result, get_from_result, walk_digikey_object, add_common_aliases,
    extract_digikey_fields, get_part_status_from_digikey,
    packaging_text_candidates, is_cut_tape_or_digireel_text,
    collect_pricing_from_object, get_digikey_pricing_candidates,
    get_digikey_price, get_digikey_price_for_quantity,
    digikey_stock_source_contains_excluded_flag,
    get_quantity_from_digikey_stock_source, get_digikey_stock,
)


# ============================================================
# PATHS / FILES
# ============================================================
# Single source of truth: config.py is frozen-aware and resolves the database
# and Symbols/ directory, falling back to the KiCad library root when they are
# not next to this file. Both used to be defined twice, and the copy in
# config.py did not handle PyInstaller freezing.
from config import (
    APP_DIR, DB_FILE, DB_BACKUP_DIR, SETTINGS_FILE, LOG_FILE,
    LEGACY_FOOTPRINT_JSON_FILE, BR_PART_NUMBERS_FILE,
    BOM_TEMPLATE_FILE, PROJECT_IMPORT_TEMPLATE_FILE,
    PART_NUMBER_REQUEST_TEMPLATE, PART_NUMBER_REQUEST_FILE,
    SYMBOL_LIBRARY_DIR, FOOTPRINT_LIBRARY_DIR, MODEL_LIBRARY_DIR, LCSC_LIBRARY_DIR,
    DEFAULT_FOOTPRINTS,
)
from db_backup import backup_database, describe_backup_result
from bom_stock import prepare_stock_column, fill_stock_row, stock_server_unreachable
# Part number request export feature removed

log = logging.getLogger("company")


# ============================================================
# DIGIKEY API SETTINGS
# ============================================================

# DigiKey lookups use the credentials configured in API-Keys.json instead of
# hard-coded values. Enter at least one client_id/client_secret pair (or a
# cid:csecret key) in the Settings window.


# ============================================================
# OPTIONS
# ============================================================

SYMBOL_OPTIONS = sorted([
    "Button_Switch:",
    "Capacitor:C",
    "Connector:",
    "Crystal:",
    "Diode:",
    "Fuse:",
    "IC:",
    "Inductor:L",
    "MCU:",
    "Mechanical:",
    "Miscellaneous:",
    "Mosfet:",
    "Opto:",
    "PTC_NTC:",
    "Relay:",
    "Resistor:R",
    "Transistor:",
])

SYMBOL_BY_COMPONENT_TYPE = {
    "companybuttonswitch": "Button_Switch:", "buttonswitch": "Button_Switch:",
    "companycapacitor": "Capacitor:C", "capacitor": "Capacitor:C",
    "companyconnector": "Connector:", "connector": "Connector:",
    "companycrystal": "Crystal:", "crystal": "Crystal:",
    "companydiode": "Diode:", "diode": "Diode:",
    "companyfuse": "Fuse:", "fuse": "Fuse:",
    "companyic": "IC:", "ic": "IC:",
    "companyinductor": "Inductor:L", "inductor": "Inductor:L",
    "companymcu": "MCU:", "mcu": "MCU:",
    "companymechanical": "Mechanical:", "mechanical": "Mechanical:",
    "companymiscellaneous": "Miscellaneous:", "miscellaneous": "Miscellaneous:",
    "companymosfet": "Mosfet:", "mosfet": "Mosfet:",
    "companyopto": "Opto:", "opto": "Opto:",
    "companyptcntc": "PTC_NTC:", "ptcntc": "PTC_NTC:",
    "companyrelay": "Relay:", "relay": "Relay:",
    "companyresistor": "Resistor:R", "resistor": "Resistor:R",
    "companytransistor": "Transistor:", "transistor": "Transistor:",
}

KICAD_SYMBOL_FILE_BY_COMPONENT_TYPE = {
    "companybuttonswitch": "Button_Switch.kicad_sym",
    "buttonswitch": "Button_Switch.kicad_sym",
    "companycapacitor": "Capacitor.kicad_sym",
    "capacitor": "Capacitor.kicad_sym",
    "companyconnector": "Connector.kicad_sym",
    "connector": "Connector.kicad_sym",
    "companycrystal": "Crystal.kicad_sym",
    "crystal": "Crystal.kicad_sym",
    "companydiode": "Diode.kicad_sym",
    "diode": "Diode.kicad_sym",
    "companyfuse": "Fuse.kicad_sym",
    "fuse": "Fuse.kicad_sym",
    "companyheader": "Header.kicad_sym",
    "header": "Header.kicad_sym",
    "companyic": "IC.kicad_sym",
    "ic": "IC.kicad_sym",
    "companyinductor": "Inductor.kicad_sym",
    "inductor": "Inductor.kicad_sym",
    "companymcu": "MCU.kicad_sym",
    "mcu": "MCU.kicad_sym",
    "companymechanical": "Mechanical.kicad_sym",
    "mechanical": "Mechanical.kicad_sym",
    "companymiscellaneous": "Miscellaneous.kicad_sym",
    "miscellaneous": "Miscellaneous.kicad_sym",
    "companymosfet": "Mosfet.kicad_sym",
    "mosfet": "Mosfet.kicad_sym",
    "companyopto": "Opto.kicad_sym",
    "opto": "Opto.kicad_sym",
    "companypad": "PAD.kicad_sym",
    "pad": "PAD.kicad_sym",
    "companyptcntc": "PTC_NTC.kicad_sym",
    "ptcntc": "PTC_NTC.kicad_sym",
    "companyrelay": "Relay.kicad_sym",
    "relay": "Relay.kicad_sym",
    "companyresistor": "Resistor.kicad_sym",
    "resistor": "Resistor.kicad_sym",
    "companytransistor": "Transistor.kicad_sym",
    "transistor": "Transistor.kicad_sym",
}

# DEFAULT_FOOTPRINTS lives in config.py (single definition).

PRICE_QUANTITY_COLUMNS = {
    "1 Adet": 1,
    "10 Adet": 10,
    "100 Adet": 100,
    "250 Adet": 250,
    "500 Adet": 500,
    "1000 Adet": 1000,
}

FORM_LABEL_ALIASES = {
    "reach": "REACH Status", "rohs": "RoHS Status",
    # Read-only record history columns (written by db.py).
    "createdat": 'Created at', "createdby": 'Created by',
    "modifiedat": 'Modified at', "modifiedby": 'Modified by',
}
BR_EXCEL_COLUMNS = ["Company Part Number", "Manufacturer", "Manufacturer Part Number", "Description"]
DEFAULT_TREE_COLUMNS = [
    {"name":"id","width":40,"location":1}, {"name":"Reference","width":100,"location":2},
    {"name":"Product Status","width":140,"location":3}, {"name":"Verified","width":90,"location":4},
    {"name":"Value","width":120,"location":5}, {"name":"Company_Part_Number","width":170,"location":6},
    {"name":"Description","width":360,"location":7}, {"name":"Manufacturer_Part_Number","width":240,"location":8},
    {"name":"Manufacturer","width":180,"location":9}, {"name":"Package","width":150,"location":10},
    {"name":"Datasheet","width":280,"location":11}, {"name":"Symbols","width":220,"location":12},
    {"name":"Footprints","width":220,"location":13}, {"name":"Operating_Temperature","width":170,"location":14},
    {"name":"Mounting_Type","width":140,"location":15}, {"name":"Tolerance","width":110,"location":16},
    {"name":"Power","width":100,"location":17}, {"name":"Voltage","width":100,"location":18},
    {"name":"Current","width":100,"location":19}, {"name":"Temperature_Coefficient","width":180,"location":20},
    {"name":"Comment","width":260,"location":21},
    {"name":"Alternate_Part_Number","width":260,"location":22},
]

# ============================================================
# GENERIC HELPERS
# ============================================================





def is_blank(value):
    return value is None or str(value).strip() == ""


def is_missing_company_part_number(value):
    text = "" if value is None else str(value).strip()
    return text == "" or text.lower() in ["none", "nan", "null", "-"] or not text.upper().startswith("CMP-")


def bool_to_db(value):
    return "1" if bool(value) else "0"


def db_to_bool(value):
    text = str(value).strip().lower()
    if text in ["1", "true", "yes", "checked", "verified"]:
        return True
    if text in ["0", "false", "no", "not verified", "notverified", "", "-", "none", "null"]:
        return False
    return False


def _widget_state(widget):
    # ttk returns a Tcl_Obj here, which never compares equal to a str; the
    # comparison below used to be always False, so read-only fields (id, and
    # now the record history fields) were never filled or cleared.
    try:
        return str(widget.cget("state"))
    except Exception:
        return ""


def widget_set_value(widget, value):
    value = "" if value is None else str(value)
    if isinstance(widget, ttk.Combobox):
        widget.set(value)
    else:
        old_state = _widget_state(widget)
        if old_state == "readonly":
            widget.configure(state="normal")
        widget.delete(0, tk.END)
        widget.insert(0, value)
        if old_state == "readonly":
            widget.configure(state="readonly")


def widget_clear(widget):
    if isinstance(widget, ttk.Combobox):
        widget.set("")
    else:
        old_state = _widget_state(widget)
        if old_state == "readonly":
            widget.configure(state="normal")
        widget.delete(0, tk.END)
        if old_state == "readonly":
            widget.configure(state="readonly")


# Layout key used for cross-table search results, which have their own column
# union plus a leading "Tablo" column and must not overwrite a table's layout.
SEARCH_LAYOUT_KEY = "__search__"


def get_default_settings():
    return {
        "footprints": list(DEFAULT_FOOTPRINTS),
        "tree_columns": normalize_tree_columns({}),
        "tree_columns_by_table": {},
        "tree_sort": {"column": "", "direction": "asc"},
    }


def normalize_tree_columns_by_table(data):
    """Per-table column layouts: {table_name: {column1, column1_width, ...}}."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for table, layout in data.items():
        if not isinstance(layout, dict):
            continue
        entries = parse_tree_columns_settings(layout)
        if not entries:
            continue
        clean[str(table)] = build_tree_columns_settings(
            [e["name"] for e in entries],
            {e["name"]: e.get("width", 150) for e in entries},
        )
    return clean


def normalize_footprint_list(footprints):
    if not isinstance(footprints, list):
        return list(DEFAULT_FOOTPRINTS)
    clean = []
    for item in footprints:
        item = str(item).strip()
        if item and item not in clean:
            clean.append(item)
    return sorted(clean) if clean else list(DEFAULT_FOOTPRINTS)


def normalize_tree_column_widths(data):
    if not isinstance(data, dict):
        return {}
    if all(not isinstance(value, dict) for value in data.values()):
        clean = {}
        for col, width in data.items():
            try:
                clean[str(col)] = max(int(width), 40)
            except Exception:
                pass
        return clean
    merged = {}
    global_widths = data.get("__global__")
    if isinstance(global_widths, dict):
        for col, width in global_widths.items():
            try:
                merged[str(col)] = max(int(width), 40)
            except Exception:
                pass
    for table_widths in data.values():
        if not isinstance(table_widths, dict):
            continue
        for col, width in table_widths.items():
            if str(col) in merged:
                continue
            try:
                merged[str(col)] = max(int(width), 40)
            except Exception:
                pass
    return merged


def normalize_tree_sort(data):
    if not isinstance(data, dict): return {"column":"","direction":"asc"}
    col=str(data.get("column","") or "")
    direction=str(data.get("direction","") or "").lower().strip()
    if direction not in ["asc","desc"]: direction = "desc" if bool(data.get("reverse", False)) else "asc"
    return {"column":col,"direction":direction}

def parse_tree_columns_settings(data):
    if not isinstance(data, dict): return []
    indexes=[]
    for k in data.keys():
        m=re.fullmatch(r"column(\d+)", str(k))
        if m: indexes.append(int(m.group(1)))
    result=[]
    for i in sorted(set(indexes)):
        name=str(data.get(f"column{i}","") or "").strip()
        if not name: continue
        try: width=max(int(data.get(f"column{i}_width",150)),40)
        except Exception: width=150
        try: loc=int(data.get(f"column{i}_location",i))
        except Exception: loc=i
        result.append({"name":name,"width":width,"location":loc})
    result.sort(key=lambda x:(x.get("location",999999),x.get("name","")))
    return result

def build_tree_columns_settings(columns, width_lookup=None):
    width_lookup=width_lookup or {}; clean={}
    for i,col in enumerate(columns or [], start=1):
        try: width=max(int(width_lookup.get(col,150)),40)
        except Exception: width=150
        clean[f"column{i}"]=str(col); clean[f"column{i}_width"]=width; clean[f"column{i}_location"]=i
    return clean

def normalize_tree_columns(data):
    entries=parse_tree_columns_settings(data)
    if not entries: entries=sorted(DEFAULT_TREE_COLUMNS, key=lambda x:x["location"])
    return build_tree_columns_settings([e["name"] for e in entries], {e["name"]:e.get("width",150) for e in entries})


def load_settings():
    """Load settings from the single settings.json next to the exe/script and migrate legacy json files."""
    settings = get_default_settings()
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings.update(data)
        except Exception:
            pass
    try:
        if os.path.isfile(LEGACY_FOOTPRINT_JSON_FILE):
            with open(LEGACY_FOOTPRINT_JSON_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            legacy_footprints = legacy.get("footprints", []) if isinstance(legacy, dict) else []
            if legacy_footprints and settings.get("footprints") == DEFAULT_FOOTPRINTS:
                settings["footprints"] = legacy_footprints
    except Exception:
        pass
    settings["footprints"] = normalize_footprint_list(settings.get("footprints", []))
    settings["tree_columns"] = normalize_tree_columns(settings.get("tree_columns", {}))
    settings["tree_columns_by_table"] = normalize_tree_columns_by_table(settings.get("tree_columns_by_table", {}))
    settings.pop("tree_column_widths", None)
    settings["tree_sort"] = normalize_tree_sort(settings.get("tree_sort", settings.get("tree_sort_state", {})))
    settings.pop("tree_sort_state", None)
    return settings


def save_settings(settings):
    clean_settings = get_default_settings()
    if isinstance(settings, dict):
        clean_settings.update(settings)
    clean_settings["footprints"] = normalize_footprint_list(clean_settings.get("footprints", []))
    clean_settings["tree_columns"] = normalize_tree_columns(clean_settings.get("tree_columns", {}))
    clean_settings["tree_columns_by_table"] = normalize_tree_columns_by_table(clean_settings.get("tree_columns_by_table", {}))
    clean_settings["tree_sort"] = normalize_tree_sort(clean_settings.get("tree_sort", {}))
    clean_settings.pop("tree_column_widths", None)
    clean_settings.pop("tree_sort_state", None)
    os.makedirs(APP_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_settings, f, ensure_ascii=False, indent=2)


def ensure_settings_file():
    """Make sure settings.json exists next to the exe/script at startup."""
    settings = load_settings()
    save_settings(settings)
    return settings


def create_default_footprint_json():
    # Legacy name kept; it now creates settings.json rather than footprints.json.
    ensure_settings_file()


def load_footprint_options():
    try:
        settings = ensure_settings_file()
        return settings.get("footprints", list(DEFAULT_FOOTPRINTS))
    except Exception:
        return list(DEFAULT_FOOTPRINTS)


from kicad_symbols import extract_kicad_symbols_from_file
# extract_lcsc_fields / get_lcsc_part_status used to be redefined here with
# slightly different behaviour; lcsc_api.py is now the single definition.
from lcsc_api import (
    get_lcsc_part_data, get_lcsc_price, get_lcsc_stock, get_lcsc_part_status,
    extract_lcsc_fields,
)


def get_kicad_symbol_file_for_component_type(component_type):
    component_key = normalize_key(component_type)
    file_name = KICAD_SYMBOL_FILE_BY_COMPONENT_TYPE.get(component_key)

    if not file_name:
        raw_name = str(component_type or "").strip()
        if raw_name:
            candidate = raw_name
            if not candidate.lower().endswith(".kicad_sym"):
                candidate += ".kicad_sym"
            candidate_path = os.path.join(SYMBOL_LIBRARY_DIR, candidate)
            if os.path.isfile(candidate_path):
                return candidate_path
        return ""

    file_path = os.path.join(SYMBOL_LIBRARY_DIR, file_name)
    if os.path.isfile(file_path):
        return file_path
    return ""


def load_kicad_symbols_for_component_type(component_type):
    file_path = get_kicad_symbol_file_for_component_type(component_type)
    if not file_path:
        return []
    return extract_kicad_symbols_from_file(file_path)


# ============================================================
# DB HELPERS (centralized in db.py)
# ============================================================
from db import (
    get_connection, get_tables, get_total_material_count, get_all_columns,
    ensure_extra_columns_for_table, ensure_extra_columns_for_all_tables,
    get_table_info, get_primary_key_columns, get_immutable_update_columns,
    get_primary_key_column, get_column_name_by_alias, get_row_value_by_alias,
    insert_data, update_data, delete_data, move_data_to_table,
    load_table_data, search_all_tables, search_selected_table_by_keywords,
    is_audit_column, set_current_user, current_user_is_admin,
    CMP_PART_NUMBER_ALIASES,
)
import users

# ============================================================
# DIGIKEY HELPERS
# ============================================================

def get_digikey_part_data(part_number):
    return digikey_api.get_digikey_part_data(part_number)


def map_lcsc_value_to_form_column(form_column, extracted, current_table=None):
    key = normalize_key(form_column)
    if key in {'companypartnumber', 'companystock', 'verified', 'symbols', 'footprints'}:
        return None
    if key == 'value' and current_table:
        value = get_value_for_table(current_table, extracted)
        return value.replace('KΩ', 'kΩ') if isinstance(value, str) else value
    return map_digikey_value_to_form_column(form_column, extracted, current_table)


def format_resistor_value_for_kicad(value):
    """Convert a DigiKey resistance value to the KiCad Ω format.

    Examples:
      "10 mOhms"  -> "10mΩ"
      "10 Ohms"   -> "10Ω"
      "10 KOhms"  -> "10KΩ"
      "10 MOhms"  -> "10MΩ"
      "1.5 GOhms" -> "1.5GΩ"
    """
    raw_value = clean_value(value).strip()
    if not raw_value:
        return ""

    # U+2126 OHM SIGN -> U+03A9 GREEK CAPITAL LETTER OMEGA
    normalized_value = raw_value.replace("Ω", "Ω")
    match = re.search(
        r"([-+]?\d+(?:[\.,]\d+)?)\s*([mMkKGg]?)(?:\s*)(?:[oO]hms?|Ω)\b",
        normalized_value,
    )
    if not match:
        return raw_value

    number = _normalize_number(match.group(1))
    multiplier = match.group(2) or ""

    # KiCad values carry no space between magnitude and unit; milliohms used
    # to be the odd one out ("10 mΩ" against "10KΩ").
    suffix = {
        "m": "mΩ",
        "k": "KΩ", "K": "KΩ",
        "M": "MΩ",
        "G": "GΩ", "g": "GΩ",
    }.get(multiplier, "Ω")

    return f"{number}{suffix}"


def format_inductor_value_for_kicad(value):
    """Convert a DigiKey inductance value to the short KiCad format.

    Examples:
      "10 nH"          -> "10nH"
      "10 uH"          -> "10µH"
      "10 µH"          -> "10µH"
      "10 Microhenry"  -> "10µH"
      "10 mH"          -> "10mH"
      "10 H"           -> "10H"
    """
    raw_value = clean_value(value).strip()
    if not raw_value:
        return ""

    normalized_value = (
        raw_value
        .replace("μ", "µ")
        .replace("u", "µ")
        .replace("UH", "µH")
        .replace("uH", "µH")
    )

    match = re.search(
        r"([-+]?\d+(?:[\.,]\d+)?)\s*"
        r"(nH|µH|mH|H|nanohenrys?|microhenrys?|millihenrys?|henrys?)\b",
        normalized_value,
        re.IGNORECASE,
    )
    if not match:
        return raw_value

    number = _normalize_number(match.group(1))
    unit = match.group(2).lower().replace("μ", "µ")

    if unit in ["nh", "nanohenry", "nanohenrys"]:
        suffix = "nH"
    elif unit in ["µh", "microhenry", "microhenrys"]:
        suffix = "µH"
    elif unit in ["mh", "millihenry", "millihenrys"]:
        suffix = "mH"
    else:
        suffix = "H"

    return f"{number}{suffix}"


def format_capacitor_value_for_kicad(value):
    """Convert a DigiKey capacitance value to the short KiCad format.

    Examples:
      "10 pF"          -> "10pF"
      "10 nF"          -> "10nF"
      "10 uF"          -> "10µF"
      "10 µF"          -> "10µF"
      "10 Microfarads" -> "10µF"
      "10 mF"          -> "10mF"
      "10 F"           -> "10F"
    """
    raw_value = clean_value(value).strip()
    if not raw_value:
        return ""

    normalized_value = (
        raw_value
        .replace("μ", "µ")
        .replace("u", "µ")
        .replace("UF", "µF")
        .replace("uF", "µF")
    )

    match = re.search(
        r"([-+]?\d+(?:[\.,]\d+)?)\s*"
        r"(pF|nF|µF|mF|F|picofarads?|nanofarads?|microfarads?|millifarads?|farads?)\b",
        normalized_value,
        re.IGNORECASE,
    )
    if not match:
        return raw_value

    number = _normalize_number(match.group(1))
    unit = match.group(2).lower().replace("μ", "µ")

    if unit in ["pf", "picofarad", "picofarads"]:
        suffix = "pF"
    elif unit in ["nf", "nanofarad", "nanofarads"]:
        suffix = "nF"
    elif unit in ["µf", "microfarad", "microfarads"]:
        suffix = "µF"
    elif unit in ["mf", "millifarad", "millifarads"]:
        suffix = "mF"
    else:
        suffix = "F"

    return f"{number}{suffix}"


def get_value_for_table(table, extracted):
    table_key = normalize_key(table)
    if table_key == "resistor":
        resistor_value = get_from_result(extracted, "Resistance", "Value", "Resistance Ohms")
        return format_resistor_value_for_kicad(resistor_value)
    if table_key == "capacitor":
        capacitor_value = get_from_result(extracted, "Capacitance", "Value")
        return format_capacitor_value_for_kicad(capacitor_value)
    if table_key == "inductor":
        inductor_value = get_from_result(extracted, "Inductance", "Value")
        return format_inductor_value_for_kicad(inductor_value)
    return get_from_result(extracted, "Value", "Manufacturer Part Number", "MPN")


def get_default_reference_for_table(table):
    table_key = normalize_key(table)
    reference_map = {
        "resistor": "R", "capacitor": "C", "inductor": "L", "ic": "U", "mcu": "U",
        "mosfet": "Q", "transistor": "Q", "diode": "D", "mechanical": "M", "opto": "O",
        "buttonswitch": "B", "button_switch": "B", "connector": "J", "crystal": "X", "fuse": "F",
    }
    return reference_map.get(table_key, "")


def map_digikey_value_to_form_column(form_column, extracted, current_table=None):
    ncol = normalize_key(form_column)
    direct = get_from_result(extracted, form_column)
    if direct:
        return direct
    if ncol == "value" and current_table:
        table_value = get_value_for_table(current_table, extracted)
        if table_value:
            return table_value
    alias_map = {
        "datasheet": ["Datasheet", "DatasheetUrl", "PrimaryDatasheet"],
        "description": ["Description", "Detailed Description", "Product Description"],
        "manufacturer": ["Manufacturer", "Mfr"],
        "manufacturerpartnumber": ["Manufacturer_Part_Number", "Manufacturer Part Number", "Mfr Part Number", "MPN"],
        "alternatepartnumber": ["Alternate_Part_Number", "Alternate Part Number", "AlternatePartNumber", "Alt Part Number", "Alt_Part_Number"],
        "operatingtemperature": ["Operating_Temperature", "Operating Temperature", "Operating Temperature Range", "Temperature - Operating"],
        "temperature": ["Temperature_Coefficient", "Temperature Coefficient", "Temperature Coefficient (TCR)", "TCR", "TC", "ppm/°C", "PPM/C", "ppm/C"],
        "mountingtype": ["Mounting_Type", "Mounting Type", "Mounting", "Termination", "Termination Style"],
        "temperaturecoefficient": ["Temperature_Coefficient", "Temperature Coefficient", "Temperature Coefficient (TCR)", "TCR", "TC", "ppm/°C", "PPM/C", "ppm/C"],
        "tolerance": ["Tolerance", "Resistance Tolerance", "Capacitance Tolerance", "Inductance Tolerance"],
        # Generic ratings first, then the diode/TVS-specific names. These were two
        # separate "power"/"current" entries in this literal, so Python kept only
        # the second and the generic aliases were silently unreachable.
        "power": [
            "Power", "Power Rating", "Power Rating (Watts)", "Power (Watts)", "Power (W)", "Wattage",
            "Power - Peak Pulse", "Peak Pulse Power", "Power Dissipation", "Power - Max",
        ],
        "package": ["Package", "Package / Case", "Supplier Device Package", "Case", "Size / Dimension"],
        "voltage": ["Voltage", "Voltage Rating", "Voltage - Rated", "Rated Voltage"],
        "current": [
            "Current", "Current Rating", "Current Rating (Max)", "Current - Rated",
            "Current - Peak Pulse (10/1000µs)", "Current - Peak Pulse", "Peak Pulse Current",
            "Peak Pulse Current (Ipp)", "Current - Average Rectified (Io)", "Average Rectified Current",
            "Current - Reverse Leakage @ Vr", "Reverse Leakage Current", "Ipp", "If", "IF",
        ],
        "currentrating": [
            "Current_Rating", "Current Rating", "Current Rating (Max)", "Current - Rated", "Current",
        ],
        "voltagerating": ["Voltage_Rating", "Voltage Rating", "Voltage - Rated", "Rated Voltage", "Voltage"],
        "reach": ["Reach", "REACH", "REACH Status"],
        "rohs": ["Rohs", "RoHS", "RoHS Status", "RoHS Compliant"],
        "forwardvoltage": ["Forward_Voltage", "Voltage - Forward (Vf) (Max) @ If", "Forward Voltage", "Forward Voltage (Vf)", "Vf", "VF"],
        "reversevoltage": ["Reverse_Voltage", "Voltage - DC Reverse (Vr) (Max)", "Voltage - Reverse Standoff (Typ)", "Reverse Voltage", "Reverse Standoff Voltage", "Working Peak Reverse Voltage", "Reverse Stand-Off Voltage", "Vr", "VR", "VRWM"],
        "breakdownvoltage": ["Breakdown_Voltage", "Voltage - Breakdown (Min)", "Breakdown Voltage", "Reverse Breakdown Voltage", "Vbr", "VBR", "V(BR)"],
        "clampingvoltage": ["Clamping_Voltage", "Voltage - Clamping (Max) @ Ipp", "Clamping Voltage", "Maximum Clamping Voltage", "Vc", "VC"],
        "type": ["Type", "Technology", "Diode Type", "Product Type"],
    }
    for alias in alias_map.get(ncol, []):
        value = get_from_result(extracted, alias)
        if value:
            return value
    for key, value in extracted.items():
        if normalize_key(key) == ncol and value not in [None, ""]:
            return value
    return None

# ============================================================
# FOOTPRINT SUGGESTION
# ============================================================

def get_entry_value_by_normalized_name(entry_widgets, *names):
    wanted = [normalize_key(name) for name in names]
    for col, widget in entry_widgets.items():
        if normalize_key(col) in wanted:
            return widget.get().strip()
    return ""


# Footprint suggestions live in footprint_suggestions.py (installed KiCad
# library first, web research only when the library has no match).

# ============================================================
# BOM HELPERS
# ============================================================

def find_bom_part_number_column(df):
    blocked = ["Company_Part_Number", "Company Part Number", "CompanyPartNumber", "CMP_Part_Number", "CMP Part Number", "CMPPartNumber", "BPN", "BPM"]
    possible = [
        "Manufacturer_Part_Number", "Manufacturer Part Number", "ManufacturerPartNumber",
        "Mfr Part Number", "Mfr_Part_Number", "MfrPartNumber", "MPN",
        "Part Number", "Part_Number", "PartNumber", "DigiKey Part Number",
        "DigiKey_Part_Number", "DigiKeyProductNumber", "DKPN", "Value"
    ]
    normalized_map = {normalize_key(col): col for col in df.columns}
    blocked_keys = [normalize_key(item) for item in blocked]
    for item in possible:
        key = normalize_key(item)
        if key in normalized_map and normalize_key(normalized_map[key]) not in blocked_keys:
            return normalized_map[key]
    return None

# ============================================================
# GUI
# ============================================================

class App:
    def __init__(self, root, user=None, backup_result=None):
        """`user` is the dict returned by the login flow.

        It is None only when the window is built without signing in (tests,
        run_smoke.py); changes are then recorded under the name "-".
        `backup_result` is passed by main(), which takes the daily backup
        before the login flow touches the database.
        """
        self.root = root
        self.user = user
        set_current_user((user or {}).get("Username", ""))
        self.root.title("Company Database GUI")
        self.root.geometry("1200x700")
        self.root.minsize(1000, 600)
        self.user_label_var = tk.StringVar(value=self.describe_user())

        # Taken before load_tables(), which may ALTER tables: the first backup
        # of the day must hold the database as it was before this version
        # touched it.
        if backup_result is None:
            backup_result = self.take_daily_backup()

        self.current_table = None
        self.columns = []
        self.entry_widgets = {}
        self.selected_key_column = None
        self.selected_key_value = None
        self.tree_item_metadata = {}
        self.tree_sort_reverse = {}

        # Create settings.json next to the exe/script as soon as the app starts.
        self.settings = ensure_settings_file()
        self.tree_columns_settings = self.load_tree_columns_settings()
        self.tree_sort = self.load_tree_sort()
        self.component_type_combo = None

        self.symbol_options = SYMBOL_OPTIONS
        self.footprint_options = load_footprint_options()

        self.bom_file_path = None
        self.bom_file_label_var = tk.StringVar(value="")
        self.stock_qty_var = tk.StringVar(value="-")
        self.company_stock_var = tk.StringVar(value="-")
        self._stock_selection = None
        self._stock_selection_poll = None
        self.unit_price_var = tk.StringVar(value="-")
        self.product_status_var = tk.StringVar(value="-")
        self.total_material_count_var = tk.StringVar(value='Total components: 0')
        self.verified_var = tk.BooleanVar(value=False)
        self.verified_combo = None
        self.verified_combo_var = tk.StringVar(value="NOT Verified")
        self.comment_text = None
        self.alternate_part_number_text = None

        # Background-task plumbing (see run_in_background).
        self._worker_thread = None
        self._cancel_event = None
        self._event_queue = queue.Queue()
        self._saved_layout_snapshot = None

        self.create_widgets()
        self.load_tables()
        self.update_total_material_count()
        self.show_backup_result(backup_result)

    # ========================================================
    # USER
    # ========================================================

    def describe_user(self):
        if not self.user:
            return 'User: -'
        role = users.ROLE_LABELS.get(self.user.get("Role"), self.user.get("Role") or "")
        return f"User: {self.user.get('Username', '-')} ({role})"

    # ========================================================
    # DAILY DATABASE BACKUP
    # ========================================================

    @staticmethod
    def take_daily_backup():
        """First start of the day: copy the database into _dbBackup/ next to the exe.

        Failures are returned, never raised — a broken backup must not stop
        the program from opening.
        """
        try:
            result = backup_database()
        except Exception as exc:
            log.exception("backup failed")
            return {"status": "error", "error": str(exc), "path": "", "deleted": []}
        log.info("backup: %s -> %s", result.get("status"), result.get("path"))
        for path in result.get("deleted", []):
            log.info("backup: old backup deleted: %s", path)
        return result

    def show_backup_result(self, result):
        try:
            message = describe_backup_result(result)
            db_name = os.path.basename(DB_FILE)
            self.status_var.set(f"{message}  |  Database: {db_name} ({os.path.dirname(DB_FILE)})")
            if result.get("status") == "error":
                messagebox.showwarning(
                    "Backup",
                    f"Daily database backup failed:\n{result.get('error')}\n\nBackup folder: {DB_BACKUP_DIR}",
                )
        except Exception:
            log.exception("backup message failed")

    # ========================================================
    # TREEVIEW COLUMN WIDTH PERSISTENCE
    # ========================================================

    def update_total_material_count(self):
        """Update the total component count in the bottom-right corner."""
        total = get_total_material_count()
        self.total_material_count_var.set(f"Total components: {total}")

    def get_tree_layout_key(self):
        """Which saved layout the Treeview currently shows.

        Cross-table search results carry an extra 'Tablo' column and an
        arbitrary column union, so they get their own key instead of
        overwriting the layout of whichever table happened to be selected.
        """
        return getattr(self, "tree_layout_key", None) or self.current_table or ""

    def load_tree_columns_settings(self, layout_key=None):
        """Column layout for one table, falling back to the shared default.

        Layouts used to be stored globally, so opening a narrow table such as
        Header (9 columns) permanently replaced the wide layout configured for
        e.g. Diode and reset every other column to the default width.
        """
        try:
            settings = ensure_settings_file()
            key = layout_key if layout_key is not None else self.get_tree_layout_key()
            per_table = settings.get("tree_columns_by_table", {})
            if isinstance(per_table, dict) and key and isinstance(per_table.get(key), dict):
                entries = parse_tree_columns_settings(per_table[key])
                if entries:
                    return entries
            return parse_tree_columns_settings(settings.get("tree_columns", {}))
        except Exception:
            return []

    def load_tree_sort(self):
        try:
            return normalize_tree_sort(ensure_settings_file().get("tree_sort", {}))
        except Exception:
            return {"column":"", "direction":"asc"}

    def save_tree_sort(self, column, direction):
        try:
            self.tree_sort = {"column": str(column or ""), "direction": "desc" if str(direction).lower()=="desc" else "asc"}
            settings = load_settings()
            settings["tree_sort"] = self.tree_sort
            save_settings(settings)
        except Exception:
            pass

    def current_tree_layout(self):
        """Current column names and widths, or None if there is nothing to save."""
        if not hasattr(self, "tree"):
            return None
        columns = list(self.tree["columns"])
        if not columns:
            return None
        widths = {}
        for col in columns:
            try:
                widths[col] = max(int(self.tree.column(col, "width")), 40)
            except Exception:
                widths[col] = 150
        return columns, widths

    def save_tree_columns(self, force=False):
        """Persist the current layout under this table's own key.

        Skips the write when nothing actually changed; this used to run a full
        settings.json read+write on every single click in the Treeview.
        """
        try:
            layout = self.current_tree_layout()
            if not layout:
                return
            columns, widths = layout
            key = self.get_tree_layout_key()
            snapshot = (key, tuple(columns), tuple(widths[c] for c in columns))
            if not force and snapshot == getattr(self, "_saved_layout_snapshot", None):
                return
            settings = load_settings()
            per_table = settings.get("tree_columns_by_table")
            if not isinstance(per_table, dict):
                per_table = {}
            entry = build_tree_columns_settings(columns, widths)
            if key:
                per_table[key] = entry
            settings["tree_columns_by_table"] = per_table
            # Keep the shared default in step with real tables only, so that a
            # narrow table or a search result never shrinks it for everyone.
            if key and key != SEARCH_LAYOUT_KEY and len(columns) >= len(parse_tree_columns_settings(settings.get("tree_columns", {}))):
                settings["tree_columns"] = entry
            settings["tree_sort"] = normalize_tree_sort(getattr(self, "tree_sort", settings.get("tree_sort", {})))
            settings.pop("tree_column_widths", None)
            save_settings(settings)
            self._saved_layout_snapshot = snapshot
            self.tree_columns_settings = parse_tree_columns_settings(entry)
        except Exception:
            pass

    def get_saved_column_width(self, col, default_width=150):
        try:
            for item in getattr(self, "tree_columns_settings", []):
                if item.get("name") == col:
                    return max(int(item.get("width", default_width)), 40)
            return default_width
        except Exception:
            return default_width

    def on_tree_button_release(self, event=None):
        # Save only when the user released a column separator after dragging;
        # otherwise every click in the table rewrote settings.json.
        try:
            if self.tree.identify_region(event.x, event.y) != "separator":
                return
        except Exception:
            pass
        self.save_tree_columns()

    def search_records(self):
        text = self.search_var.get().strip()
        if not text:
            messagebox.showwarning('Warning', 'Enter a value to search the database.')
            return
        try:
            columns, rows, items = search_all_tables(text)
            self.setup_tree_columns(columns, layout_key=SEARCH_LAYOUT_KEY)
            display_columns = list(self.tree["columns"])
            for values, item in zip(rows, items):
                row_dict = {'Table': item["table"]}
                row_dict.update(item["row_dict"])
                display_values = [row_dict.get(col, "") for col in display_columns]
                iid = self.tree.insert("", tk.END, values=display_values)
                self.tree_item_metadata[iid] = {"table": item["table"], "row_dict": item["row_dict"]}
            self.apply_saved_tree_sort()
            self.status_var.set(f"Database search completed. {len(rows)} records found.")
            if not rows:
                messagebox.showinfo('No results', 'No matching records found in the local database.')
        except Exception as e:
            messagebox.showerror('Error', f"Database search failed:\n{e}")

    def create_widgets(self):
        from ui.widgets import build_main_ui
        # Must run before any combobox is created so the named styles exist.
        self.create_combobox_styles()
        build_main_ui(self)

    # ========================================================
    # BACKGROUND WORK
    # ========================================================
    # Supplier lookups and BOM runs used to execute on the Tk main thread with
    # update_idletasks() inside the loop, which froze the window for the whole
    # job and offered no way to stop it. Workers now run on a thread and talk
    # back through a queue that the main thread drains; only the main thread
    # ever touches a widget.

    def cancel_background_task(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.status_var.set('Cancelling...')

    def is_busy(self):
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def _set_busy(self, busy, total=0):
        for button in getattr(self, "busy_buttons", []):
            try:
                button.configure(state=tk.DISABLED if busy else tk.NORMAL)
            except Exception:
                pass
        if getattr(self, "progress_bar", None) is not None:
            if busy:
                self.progress_bar.configure(maximum=max(total, 1), value=0)
                self.progress_bar.grid()
                self.cancel_button.configure(state=tk.NORMAL)
                self.cancel_button.grid()
            else:
                self.progress_bar.grid_remove()
                self.cancel_button.grid_remove()

    def run_in_background(self, worker, on_success, on_error=None, total=0, busy_message=""):
        """Run `worker(report, is_cancelled)` on a thread.

        `worker` must not touch any widget. It reports progress by calling
        `report(message, done)` and should poll `is_cancelled()` in its loop.
        `on_success` / `on_error` are invoked back on the main thread.
        """
        if self.is_busy():
            messagebox.showwarning('Busy', 'An operation is in progress. Wait for it to finish or cancel it.')
            return
        self._event_queue = queue.Queue()
        self._cancel_event = threading.Event()
        event_queue = self._event_queue
        cancel_event = self._cancel_event

        def report(message="", done=None):
            event_queue.put(("progress", (message, done)))

        def run():
            try:
                result = worker(report, cancel_event.is_set)
                event_queue.put(("done", result))
            except Exception as exc:  # surfaced on the main thread
                event_queue.put(("error", exc))

        if busy_message:
            self.status_var.set(busy_message)
        self._set_busy(True, total=total)
        self._worker_thread = threading.Thread(target=run, daemon=True)
        self._worker_thread.start()
        self._pump_events(on_success, on_error)

    def _pump_events(self, on_success, on_error):
        try:
            while True:
                kind, payload = self._event_queue.get_nowait()
                if kind == "progress":
                    message, done = payload
                    if message:
                        self.status_var.set(message)
                    if done is not None and getattr(self, "progress_bar", None) is not None:
                        self.progress_bar.configure(value=done)
                elif kind == "done":
                    self._set_busy(False)
                    self._worker_thread = None
                    self._cancel_event = None
                    on_success(payload)
                    return
                elif kind == "error":
                    self._set_busy(False)
                    self._worker_thread = None
                    self._cancel_event = None
                    if on_error:
                        on_error(payload)
                    else:
                        messagebox.showerror('Error', str(payload))
                        self.status_var.set('Operation failed.')
                    return
        except queue.Empty:
            pass
        self.root.after(100, lambda: self._pump_events(on_success, on_error))
        

    def open_settings_window(self):
        try:
            from ui.settings_window import open_settings
            open_settings(self)
        except Exception as e:
            messagebox.showerror('Settings', f"Could not open settings:\n{e}")

    def reload_api_keys(self):
        try:
            # Reload the shared ApiKeyManager used by the application
            from api_manager import reload_manager
            from config import API_KEYS_FILE
            return reload_manager(API_KEYS_FILE)
        except Exception:
            return False

    # ========================================================
    # UI HELPERS
    # ========================================================

    def clear_digikey_side_info(self):
        self.stock_qty_var.set("-")
        self.unit_price_var.set("-")
        self.product_status_var.set("-")

    def update_digikey_side_info(self, product):
        self.stock_qty_var.set(str(get_digikey_stock(product) or "-"))
        self.unit_price_var.set(str(get_digikey_price(product) or "-"))
        self.product_status_var.set(str(get_part_status_from_digikey(product) or "-"))

    def open_datasheet_link(self, event=None):
        """Open the link in the Datasheet field in the default web browser."""
        datasheet_widget = None
        for col, widget in self.entry_widgets.items():
            if normalize_key(col) == "datasheet":
                datasheet_widget = widget
                break
        if not datasheet_widget:
            messagebox.showwarning("Datasheet", 'Datasheet field not found.')
            return
        url = datasheet_widget.get().strip()
        if not url:
            messagebox.showwarning("Datasheet", 'The datasheet link is empty.')
            return
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = "https://" + url
        try:
            webbrowser.open_new_tab(url)
            self.status_var.set(f"Datasheet opened: {url}")
        except Exception as e:
            messagebox.showerror("Datasheet", f"Could not open the datasheet link:\n{e}")

    def get_comment_value(self):
        return self.comment_text.get("1.0", tk.END).strip() if self.comment_text else ""

    def set_comment_value(self, value):
        if self.comment_text:
            self.comment_text.delete("1.0", tk.END)
            self.comment_text.insert("1.0", "" if value is None else str(value))

    def get_alternate_part_number_value(self):
        return self.alternate_part_number_text.get("1.0", tk.END).strip() if self.alternate_part_number_text else ""

    def set_alternate_part_number_value(self, value):
        if self.alternate_part_number_text:
            self.alternate_part_number_text.delete("1.0", tk.END)
            self.alternate_part_number_text.insert("1.0", "" if value is None else str(value))

    def get_symbol_suggestion_for_component_type(self):
        component_type = self.component_type_combo.get().strip() if self.component_type_combo else ""
        if not component_type:
            component_type = self.current_table or ""
        return SYMBOL_BY_COMPONENT_TYPE.get(normalize_key(component_type), "")

    def get_current_component_type_for_symbol_suggestion(self):
        component_type = ""
        if self.component_type_combo:
            component_type = self.component_type_combo.get().strip()
        if not component_type:
            component_type = self.current_table or ""
        return component_type

    def get_symbol_suggestions_from_kicad_file(self):
        component_type = self.get_current_component_type_for_symbol_suggestion()
        file_path = get_kicad_symbol_file_for_component_type(component_type)
        if not file_path:
            try:
                self.status_var.set(f"Symbol file not found: {component_type} / {SYMBOL_LIBRARY_DIR}")
            except Exception:
                pass
            return []
        try:
            self.status_var.set(f"Reading symbol file: {os.path.basename(file_path)}")
        except Exception:
            pass
        return extract_kicad_symbols_from_file(file_path)

    def open_symbol_suggestion_window(self, suggestions):
        if not suggestions:
            messagebox.showwarning('Symbol suggestions', 'No symbols found in the .kicad_sym file for this table.')
            return

        window = tk.Toplevel(self.root)
        window.title('Symbol suggestions')
        window.geometry("1050x560")
        window.minsize(800, 420)
        window.transient(self.root)
        window.grab_set()

        ttk.Label(window, text='KiCad symbol suggestions', font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        search_var = tk.StringVar(value="")
        search_entry = ttk.Entry(window, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=(0, 8))

        from ui.library_preview import LibraryPreview
        split = ttk.Panedwindow(window, orient='horizontal')
        split.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        list_frame = ttk.Frame(split, width=400)
        split.add(list_frame, weight=2)
        preview_frame = ttk.LabelFrame(split, text='Symbol preview', width=600)
        split.add(preview_frame, weight=3)
        preview = LibraryPreview(preview_frame)
        preview.pack(fill=tk.BOTH, expand=True)
        symbol_file = get_kicad_symbol_file_for_component_type(self.get_current_component_type_for_symbol_suggestion())
        y_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        symbol_listbox = tk.Listbox(list_frame, yscrollcommand=y_scroll.set, exportselection=False)
        y_scroll.config(command=symbol_listbox.yview)
        symbol_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def update_preview(event=None):
            selected = symbol_listbox.curselection()
            preview.show_file(symbol_file if selected else None,
                              symbol_listbox.get(selected[0]) if selected else None)

        def refresh_list():
            keyword = search_var.get().strip().lower()
            symbol_listbox.delete(0, tk.END)
            for item in suggestions:
                if not keyword or keyword in item.lower():
                    symbol_listbox.insert(tk.END, item)
            if symbol_listbox.size():
                symbol_listbox.selection_set(0)
            update_preview()

        def apply_selected(event=None):
            selected = symbol_listbox.curselection()
            if not selected:
                messagebox.showwarning('Select symbol', 'Select a symbol from the list.')
                return
            symbol_name = symbol_listbox.get(selected[0])
            symbols_widget = next((w for c, w in self.entry_widgets.items() if normalize_key(c) in ["symbols", "symbol"]), None)
            if not symbols_widget:
                messagebox.showwarning('Symbols field missing', 'The record form has no Symbols field.')
                return
            widget_set_value(symbols_widget, symbol_name)
            self.status_var.set(f"Symbols field updated: {symbol_name}")
            window.destroy()

        search_var.trace_add("write", lambda *args: refresh_list())
        symbol_listbox.bind("<Double-Button-1>", apply_selected)
        symbol_listbox.bind('<<ListboxSelect>>', update_preview)

        button_frame = ttk.Frame(window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(button_frame, text='Select', command=apply_selected).pack(side=tk.LEFT)
        ttk.Button(button_frame, text='Close', command=window.destroy).pack(side=tk.RIGHT)

        refresh_list()
        if symbol_listbox.size() > 0:
            symbol_listbox.selection_set(0)
        search_entry.focus_set()

    def on_symbols_label_clicked(self, event=None):
        suggestions = self.get_symbol_suggestions_from_kicad_file()
        if suggestions:
            self.open_symbol_suggestion_window(suggestions)
            return

        suggestion = self.get_symbol_suggestion_for_component_type()
        component_type = self.get_current_component_type_for_symbol_suggestion() or "-"
        if not suggestion:
            messagebox.showwarning('No symbol suggestions', f"No suitable symbol found for this component type.\n\nComponent type: {component_type}")
            return
        symbols_widget = next((w for c, w in self.entry_widgets.items() if normalize_key(c) in ["symbols", "symbol"]), None)
        if not symbols_widget:
            messagebox.showwarning('Symbols field missing', 'The record form has no Symbols field.')
            return
        if messagebox.askyesno('Symbol suggestions', f"No symbols found in the .kicad_sym file.\n\nUse the following symbol for this component type?\n\n{suggestion}"):
            widget_set_value(symbols_widget, suggestion)
            self.status_var.set(f"Suggested symbol applied: {suggestion}")

    # ========================================================
    # TABLE / FORM
    # ========================================================

    def load_tables(self):
        try:
            ensure_extra_columns_for_all_tables()
            tables = get_tables()
            self.table_combo["values"] = tables
            if self.component_type_combo:
                self.component_type_combo["values"] = tables
            if tables:
                current = self.table_combo.get()
                self.table_combo.set(current if current in tables else tables[0])
                self.on_table_selected()
            else:
                messagebox.showwarning('Warning', 'No tables found in the database.')
        except Exception as e:
            messagebox.showerror('Error', f"Could not load tables:\n{e}")

    def on_table_selected(self, event=None):
        try:
            self.save_tree_columns()
            self.current_table = self.table_combo.get()
            if not self.current_table:
                return
            ensure_extra_columns_for_table(self.current_table)
            self.prepare_form_for_table(self.current_table)
            self.load_records()
            self.status_var.set(f"Selected table: {self.current_table}")
        except Exception as e:
            messagebox.showerror('Error', f"Could not load the table:\n{e}")

    def prepare_form_for_table(self, table):
        self._stock_selection = None
        self.company_stock_var.set("-")
        ensure_extra_columns_for_table(table)
        self.current_table = table
        self.selected_key_column = get_primary_key_column(table)
        self.selected_key_value = None
        self.columns = get_all_columns(table)
        self.create_form_fields()
        if self.component_type_combo:
            tables = get_tables()
            self.component_type_combo["values"] = tables
            self.component_type_combo.set(table)




    def create_combobox_styles(self):
        style = ttk.Style()

        # GREEN combobox
        style.configure(
            "Green.TCombobox",
            fieldbackground="#008000",
            background="#008000",
            foreground="black",
            selectbackground="#008000",
            selectforeground="black"
        )

        style.map(
            "Green.TCombobox",
            fieldbackground=[
                ("readonly", "#008000"),
                ("focus", "#008000"),
                ("active", "#008000")
            ],
            background=[
                ("readonly", "#008000"),
                ("focus", "#008000"),
                ("active", "#008000")
            ],
            foreground=[
                ("disabled", "black"),
                ("!disabled", "black"),
                ("readonly", "black"),
                ("focus", "black"),
                ("active", "black")
            ],
            selectbackground=[
                ("readonly", "#008000"),
                ("focus", "#008000"),
                ("active", "#008000")
            ],
            selectforeground=[
                ("disabled", "black"),
                ("!disabled", "black"),
                ("readonly", "black"),
                ("focus", "black"),
                ("active", "black")
            ]
        )

        # RED combobox
        style.configure(
            "Red.TCombobox",
            fieldbackground="#C00000",
            background="#C00000",
            foreground="black",
            selectbackground="#C00000",
            selectforeground="black"
        )

        style.map(
            "Red.TCombobox",
            fieldbackground=[
                ("readonly", "#C00000"),
                ("focus", "#C00000"),
                ("active", "#C00000")
            ],
            background=[
                ("readonly", "#C00000"),
                ("focus", "#C00000"),
                ("active", "#C00000")
            ],
            foreground=[
                ("disabled", "black"),
                ("!disabled", "black"),
                ("readonly", "black"),
                ("focus", "black"),
                ("active", "black")
            ],
            selectbackground=[
                ("readonly", "#C00000"),
                ("focus", "#C00000"),
                ("active", "#C00000")
            ],
            selectforeground=[
                ("disabled", "black"),
                ("!disabled", "black"),
                ("readonly", "black"),
                ("focus", "black"),
                ("active", "black")
            ]
        )




    def sync_verified_combo_from_var(self):
        """Keep the Verified checkbox and the record form's Verified combobox on the same variable."""
        verified_text = "Verified" if self.verified_var.get() else "NOT Verified"
        if hasattr(self, "verified_combo_var") and self.verified_combo_var:
            self.verified_combo_var.set(verified_text)
        if self.verified_combo:
            self.verified_combo.set(verified_text)
            self.apply_verified_combo_style()
        self.apply_verified_permissions()

    def apply_verified_permissions(self):
        admin = current_user_is_admin()
        if self.verified_combo:
            self.verified_combo.configure(state="readonly" if admin else "disabled")
        locked = self.selected_key_value is not None and self.verified_var.get() and not admin
        for name in ("save_record_button", "update_record_button", "delete_record_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.configure(state="disabled" if locked else "normal")

    def apply_verified_combo_style(self):
        """Green when Verified, red otherwise.

        create_combobox_styles() defines these styles but nothing ever called
        it, and the only method that applied them was shadowed by a later
        redefinition — so the combobox never actually changed colour.
        """
        if not self.verified_combo:
            return
        try:
            style_name = "Green.TCombobox" if self.verified_var.get() else "Red.TCombobox"
            self.verified_combo.configure(style=style_name)
        except Exception:
            pass

    def on_verified_combo_changed(self, event=None):
        """Update the Verified checkbox when the record form's Verified combobox changes."""
        if hasattr(self, "verified_combo_var") and self.verified_combo_var:
            value = self.verified_combo_var.get().strip()
        elif self.verified_combo:
            value = self.verified_combo.get().strip()
        else:
            return
        self.verified_var.set(value == "Verified")
        self.sync_verified_combo_from_var()

    def on_verified_checkbox_changed(self):
        """Update the record form's Verified combobox when the Verified checkbox changes."""
        self.sync_verified_combo_from_var()

    def add_verified_combobox_row(self, row, width):
        """Add the Verified combobox row above the id field in the record form."""
        ttk.Label(
            self.form_frame,
            text="Verified"
        ).grid(
            row=row,
            column=0,
            sticky=tk.W,
            pady=3,
            padx=(0, 5)
        )

        self.verified_combo = ttk.Combobox(
            self.form_frame,
            values=["Verified", "NOT Verified"],
            textvariable=self.verified_combo_var,
            width=width,
            state="readonly"
        )
        self.verified_combo.grid(
            row=row,
            column=1,
            sticky=tk.EW,
            pady=3
        )
        self.verified_combo.bind("<<ComboboxSelected>>", self.on_verified_combo_changed)
        popup = self.verified_combo.tk.call("ttk::combobox::PopdownWindow", self.verified_combo)
        self.verified_combo.tk.call(f"{popup}.f.l", "configure",
                                    "-foreground", "black", "-selectforeground", "black")
        self.sync_verified_combo_from_var()
        self.apply_verified_combo_style()
        return row + 1

    def create_form_fields(self):
        for widget in self.form_frame.winfo_children():
            widget.destroy()

        self.entry_widgets.clear()

        # Outer width of every entry and combobox is set here.
        FORM_WIDGET_PIXEL_WIDTH = 205

        # The grid column determines the real width.
        FORM_WIDGET_CHAR_WIDTH = 1

        self.form_frame.columnconfigure(0, weight=0)
        self.form_frame.columnconfigure(1, weight=0, minsize=FORM_WIDGET_PIXEL_WIDTH)

        ttk.Label(
            self.form_frame,
            text="Component Type"
        ).grid(
            row=0,
            column=0,
            sticky=tk.W,
            pady=3,
            padx=(0, 5)
        )

        self.component_type_combo = ttk.Combobox(
            self.form_frame,
            state="readonly",
            width=FORM_WIDGET_CHAR_WIDTH
        )
        self.component_type_combo.grid(
            row=0,
            column=1,
            sticky=tk.EW,
            pady=3
        )

        try:
            tables = get_tables()
            self.component_type_combo["values"] = tables
            if self.current_table in tables:
                self.component_type_combo.set(self.current_table)
        except Exception:
            self.component_type_combo["values"] = []

        readonly_keys = get_immutable_update_columns(
            self.current_table,
            self.selected_key_column
        )

        row = 1

        for col in self.columns:
            key = normalize_key(col)

            if key in ["verified", "comment", "alternatepartnumber", "companystock"]:
                continue

            # The Verified combobox goes ABOVE the id field in the record form.
            # It shares self.verified_var with the Verified checkbox in the top bar.
            if key == "id":
                row = self.add_verified_combobox_row(row, FORM_WIDGET_CHAR_WIDTH)

            label_text = FORM_LABEL_ALIASES.get(key, col)

            label = ttk.Label(
                self.form_frame,
                text=label_text
            )
            label.grid(
                row=row,
                column=0,
                sticky=tk.W,
                pady=3,
                padx=(0, 5)
            )

            if key in ["symbols", "symbol"]:
                label.configure(cursor="hand2")
                label.bind("<Button-1>", self.on_symbols_label_clicked)

                kicad_symbol_suggestions = self.get_symbol_suggestions_from_kicad_file()
                if not kicad_symbol_suggestions:
                    kicad_symbol_suggestions = self.symbol_options

                widget = ttk.Combobox(
                    self.form_frame,
                    values=kicad_symbol_suggestions,
                    width=FORM_WIDGET_CHAR_WIDTH,
                    state="normal"
                )

            elif key in ["footprints", "footprint"]:
                label.configure(cursor="hand2")
                label.bind("<Button-1>", self.on_footprints_label_clicked)

                widget = ttk.Combobox(
                    self.form_frame,
                    values=self.footprint_options,
                    width=FORM_WIDGET_CHAR_WIDTH,
                    state="normal"
                )

            elif key == "datasheet":
                label.configure(cursor="hand2")
                label.bind("<Button-1>", self.open_datasheet_link)

                widget = ttk.Entry(
                    self.form_frame,
                    width=FORM_WIDGET_CHAR_WIDTH
                )

            elif key == "mountingtype":
                widget = ttk.Combobox(
                    self.form_frame,
                    values=["SMD", "THT"],
                    width=FORM_WIDGET_CHAR_WIDTH,
                    state="normal"
                )

            else:
                if key in readonly_keys:
                    widget = ttk.Entry(
                        self.form_frame,
                        width=FORM_WIDGET_CHAR_WIDTH,
                        state="readonly"
                    )
                else:
                    widget = ttk.Entry(
                        self.form_frame,
                        width=FORM_WIDGET_CHAR_WIDTH
                    )

            # IMPORTANT:
            # This block must stay inside the for loop.
            widget.grid(
                row=row,
                column=1,
                sticky=tk.EW,
                pady=3
            )

            self.entry_widgets[col] = widget
            row += 1


    # ========================================================
    # SORTABLE / FIXED WIDTH TREEVIEW
    # ========================================================

    def get_tree_sort_key(self, value):
        if value is None:
            return True, 2, ""
        text = str(value).strip()
        if text == "" or text.lower() in ["none", "nan", "null", "-"]:
            return True, 2, ""
        numeric_text = text
        for token in ["₺", "$", "€", "USD", "TL", "pcs", "PCS", "adet", 'Quantity', " "]:
            numeric_text = numeric_text.replace(token, "")
        if "," in numeric_text and "." in numeric_text:
            if numeric_text.rfind(",") > numeric_text.rfind("."):
                numeric_text = numeric_text.replace(".", "").replace(",", ".")
            else:
                numeric_text = numeric_text.replace(",", "")
        elif "," in numeric_text and "." not in numeric_text:
            numeric_text = numeric_text.replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", numeric_text)
        if match:
            try:
                return False, 0, float(match.group())
            except Exception:
                pass
        return False, 1, text.casefold()

    def sort_treeview_by_column(self, column, save_state=True, direction_override=None):
        self.save_tree_columns()
        columns = list(self.tree["columns"])
        if column not in columns:
            return
        idx = columns.index(column)
        if direction_override in ["asc", "desc"]:
            reverse = direction_override == "desc"
            self.tree_sort_reverse[column] = not reverse
        else:
            reverse = self.tree_sort_reverse.get(column, False)
            self.tree_sort_reverse[column] = not reverse
        non_empty, empty = [], []
        for iid in self.tree.get_children(""):
            values = self.tree.item(iid, "values")
            if idx >= len(values):
                empty.append(iid)
                continue
            is_empty, type_rank, sort_value = self.get_tree_sort_key(values[idx])
            if is_empty:
                empty.append(iid)
            else:
                non_empty.append((type_rank, sort_value, iid))
        non_empty.sort(key=lambda item: (item[0], item[1]), reverse=reverse)
        ordered = [item[2] for item in non_empty] + empty
        for pos, iid in enumerate(ordered):
            self.tree.move(iid, "", pos)
        for col in columns:
            width = int(self.tree.column(col, "width"))
            self.tree.heading(col, text=self._get_display_name(col), command=lambda c=col: self.sort_treeview_by_column(c))
            self.tree.column(col, width=width, minwidth=40, stretch=False)
        arrow = "▼" if reverse else "▲"
        self.tree.heading(column, text=f"{self._get_display_name(column)} {arrow}", command=lambda c=column: self.sort_treeview_by_column(c))
        self.save_tree_columns()
        if save_state:
            self.save_tree_sort(column, "desc" if reverse else "asc")
        self.status_var.set(f"'{column}' sorted: {'descending / Z-A' if reverse else 'ascending / A-Z'}")

    def apply_saved_tree_sort(self):
        try:
            self.tree_sort = normalize_tree_sort(load_settings().get("tree_sort", {}))
            column = self.tree_sort.get("column", "")
            direction = self.tree_sort.get("direction", "asc")
            if column and column in list(self.tree["columns"]):
                self.sort_treeview_by_column(column, save_state=False, direction_override=direction)
        except Exception:
            pass

    def get_display_columns_from_settings(self, db_columns):
        try:
            configured = getattr(self, "tree_columns_settings", None)
            if not configured:
                configured = self.load_tree_columns_settings()
            configured_names = [item["name"] for item in configured if item.get("name") in db_columns]
            remaining = [col for col in db_columns if col not in configured_names]
            return configured_names + remaining
        except Exception:
            return list(db_columns)

    # Display name mapping for column headers (underscore -> space)
    _DISPLAY_NAME_MAP = {
        "Company_Part_Number": "Company Part Number",
        "Manufacturer_Part_Number": "Manufacturer Part Number",
        "Operating_Temperature": "Operating Temperature",
        "Mounting_Type": "Mounting Type",
        "Temperature_Coefficient": "Temperature Coefficient",
        "Alternate_Part_Number": "Alternate Part Number",
        "Product_Status": "Product Status",
        "Last_Login_At": "Last Login",
        "Created_At": "Created At",
        "Created_By": "Created By",
        "Modified_At": "Modified At",
        "Modified_By": "Modified By",
    }

    def _get_display_name(self, col):
        """Get display name for column header."""
        return self._DISPLAY_NAME_MAP.get(col, col.replace("_", " "))

    def setup_tree_columns(self, columns, layout_key=None):
        # Save the outgoing layout under the key it belonged to, then switch.
        self.save_tree_columns()
        self.tree_layout_key = layout_key if layout_key is not None else (self.current_table or "")
        self.tree_columns_settings = self.load_tree_columns_settings(self.tree_layout_key)
        self.tree.delete(*self.tree.get_children())
        self.tree_item_metadata.clear()
        self.tree_sort_reverse.clear()
        display_columns = self.get_display_columns_from_settings(list(columns))
        self.tree["columns"] = display_columns
        self._saved_layout_snapshot = None
        for col in display_columns:
            width = self.get_saved_column_width(col, default_width=150)
            self.tree.heading(col, text=self._get_display_name(col), command=lambda c=col: self.sort_treeview_by_column(c))
            self.tree.column(col, width=width, minwidth=40, stretch=False)

    def load_records(self):
        if not self.current_table:
            return
        try:
            columns, rows = load_table_data(self.current_table)
            self.setup_tree_columns(columns)
            display_columns = list(self.tree["columns"])
            for row in rows:
                row_dict = dict(zip(columns, row))
                values = [row_dict.get(col, "") for col in display_columns]
                iid = self.tree.insert("", tk.END, values=values)
                self.tree_item_metadata[iid] = {"table": self.current_table, "row_dict": row_dict}
            self.apply_saved_tree_sort()
            self.status_var.set(f"{len(rows)} records loaded.")
        except Exception as e:
            messagebox.showerror('Error', f"Could not load records:\n{e}")

    def get_form_data(self):
        # Created_*/Modified_* are shown read-only in the form but are written
        # by db.py alone, so they never travel with the form data.
        data = {col: widget.get().strip() for col, widget in self.entry_widgets.items()
                if not is_audit_column(col) and normalize_key(col) != "companystock"}
        data["Verified"] = bool_to_db(self.verified_var.get())
        data["Comment"] = self.get_comment_value()
        data["Alternate_Part_Number"] = self.get_alternate_part_number_value()
        return data

    def is_form_completely_empty(self, data):
        for col, value in data.items():
            if col.lower() in ["id", "verified", "comment"] or is_audit_column(col):
                continue
            if str(value).strip():
                return False
        return not self.get_comment_value().strip()

    def clear_form(self):
        self._stock_selection = None
        for widget in self.entry_widgets.values():
            widget_clear(widget)
        self.selected_key_value = None
        selected = self.tree.selection()
        if selected:
            self.tree.selection_remove(selected)
        if self.component_type_combo and self.current_table:
            self.component_type_combo.set(self.current_table)
        self.verified_var.set(False)
        self.company_stock_var.set("-")
        self.sync_verified_combo_from_var()
        self.set_comment_value("")
        self.set_alternate_part_number_value("")
        self.clear_digikey_side_info()
        self.status_var.set("Form cleared.")

    def add_record(self):
        if not self.current_table:
            messagebox.showwarning('Warning', 'Select a table first.')
            return
        try:
            target = self.component_type_combo.get().strip() or self.current_table
            ensure_extra_columns_for_table(target)
            data = self.get_form_data()
            if self.is_form_completely_empty(data):
                messagebox.showwarning('Warning', 'Enter at least one field before adding a record.')
                return
            target_cols = get_all_columns(target)
            payload = {col: data[col] for col in target_cols if col.lower() != "id" and col in data}
            if not payload:
                messagebox.showwarning('Warning', 'No compatible fields found in the destination table.')
                return
            insert_data(target, payload)
            self.current_table = target
            self.table_combo.set(target)
            self.prepare_form_for_table(target)
            self.load_records()
            self.clear_form()
            self.update_total_material_count()
            messagebox.showinfo('Success', f"Record added to {target}.")
        except sqlite3.IntegrityError as e:
            messagebox.showerror('Database error', f"Could not add the record.\nA unique field may already exist.\n\n{e}")
        except Exception as e:
            messagebox.showerror('Error', f"Could not add the record:\n{e}")

    def update_record(self):
        if not self.current_table:
            messagebox.showwarning('Warning', 'Select a table first.')
            return
        if self.selected_key_value is None:
            messagebox.showwarning('Warning', 'Select a record to update or move.')
            return
        try:
            data = self.get_form_data()
            if self.is_form_completely_empty(data):
                messagebox.showwarning('Warning', 'Enter data before updating or moving a record.')
                return
            target = self.component_type_combo.get().strip() or self.current_table
            if target != self.current_table:
                if not messagebox.askyesno('Move record', f"Move this record from '{self.current_table}' to '{target}'?"):
                    return
                move_data_to_table(self.current_table, target, data, self.selected_key_column, self.selected_key_value)
                self.current_table = target
                self.table_combo.set(target)
                self.prepare_form_for_table(target)
                self.load_records()
                self.clear_form()
                messagebox.showinfo('Success', f"Record moved to the '{target}' table.")
                return
            affected = update_data(self.current_table, data, self.selected_key_column, self.selected_key_value)
            if affected == 0:
                messagebox.showinfo('Information', 'Record not found or no editable fields available.')
                return
            self.load_records()
            self.clear_form()
            messagebox.showinfo('Success', 'Record updated.')
        except sqlite3.IntegrityError as e:
            messagebox.showerror('Database error', f"Operation failed.\nA unique field may already exist, or the destination table requires additional fields.\n\n{e}")
        except Exception as e:
            messagebox.showerror('Error', f"Could not update or move the record:\n{e}")

    def delete_record(self):
        if not self.current_table:
            messagebox.showwarning('Warning', 'Select a table first.')
            return
        if self.selected_key_value is None:
            messagebox.showwarning('Warning', 'Select a record to delete.')
            return
        if not messagebox.askyesno('Confirm', 'Delete the selected record?'):
            return
        try:
            delete_data(self.current_table, self.selected_key_column, self.selected_key_value)
            self.load_records()
            self.clear_form()
            self.update_total_material_count()
            messagebox.showinfo('Success', 'Record deleted.')
        except Exception as e:
            messagebox.showerror('Error', f"Could not delete the record:\n{e}")

    def on_row_selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            self._stock_selection = None
            return
        meta = self.tree_item_metadata.get(selected[0])
        if not meta:
            return
        table = meta["table"]
        row_dict = meta["row_dict"]
        if table != self.current_table:
            self.current_table = table
            self.table_combo.set(table)
            self.prepare_form_for_table(table)
        if self.component_type_combo:
            self.component_type_combo.set(table)
        for col in self.columns:
            if normalize_key(col) in ["verified", "comment"]:
                continue
            widget = self.entry_widgets.get(col)
            if widget:
                widget_set_value(widget, row_dict.get(col, ""))

        # Based on the reading logic that worked in KicadDB_Kicad_Digikey_logo.py.
        verified_value = row_dict.get("Verified", "")
        if verified_value in [None, ""]:
            verified_value = get_row_value_by_alias(row_dict, ["Verified", "verified", "VERIFIED"])

        self.verified_var.set(db_to_bool(verified_value))
        stock = row_dict.get("Company_Stock")
        self.company_stock_var.set("-" if stock is None else f"{stock:g}" if isinstance(stock, (int, float)) else str(stock))
        try:
            self.root.after_idle(self.sync_verified_combo_from_var)
        except Exception:
            pass

        self.set_comment_value(row_dict.get("Comment", ""))
        alternate_part_number_value = get_row_value_by_alias(
            row_dict,
            ["Alternate_Part_Number", "Alternate Part Number", "AlternatePartNumber", "Alt_Part_Number", "Alt Part Number"]
        )
        self.set_alternate_part_number_value(alternate_part_number_value)
        self.selected_key_column = get_primary_key_column(self.current_table)
        self.selected_key_value = row_dict.get(self.selected_key_column) if self.selected_key_column in row_dict else None
        self.status_var.set(f"Selected record: {self.current_table} / {self.selected_key_column} = {self.selected_key_value}")
        br_column = get_column_name_by_alias(row_dict.keys(), CMP_PART_NUMBER_ALIASES)
        code = str(row_dict.get(br_column) or "").strip()
        self._stock_selection = (selected[0], table, br_column, code, object()) if code and code != "-" else None
        self._start_selected_stock_check()

    def _start_selected_stock_check(self):
        # One request at a time; fast selection changes collapse to the latest row.
        if self._stock_selection is None or self._stock_selection_poll is not None:
            return
        if self.is_busy():
            def retry():
                self._stock_selection_poll = None
                self._start_selected_stock_check()
            self._stock_selection_poll = self.root.after(150, retry)
            return
        request = self._stock_selection
        results = queue.Queue()

        def worker():
            try:
                results.put((fetch_selected_stock(request[3]), None))
            except Exception as exc:
                results.put((None, exc))

        def poll():
            try:
                quantity, error = results.get_nowait()
            except queue.Empty:
                self._stock_selection_poll = self.root.after(100, poll)
                return
            self._stock_selection_poll = None
            if request != self._stock_selection or self.is_busy():
                self._start_selected_stock_check()
                return
            self._apply_selected_stock_result(request, quantity, error)

        threading.Thread(target=worker, daemon=True).start()
        self._stock_selection_poll = self.root.after(100, poll)

    def _apply_selected_stock_result(self, request, quantity, error):
        iid, table, br_column, code, _token = request
        if request != self._stock_selection or tuple(self.tree.selection()) != (iid,):
            return
        if error is not None or quantity is None:
            self.status_var.set(f"{code}: {'stock lookup failed' if error else 'local stock record not found'}; the last saved value was retained.")
            return
        try:
            affected = update_company_stock(table, br_column, code, quantity)
            if not affected:
                return
            # Update visible rows without reloading the form or discarding edits.
            for item, meta in self.tree_item_metadata.items():
                if meta["table"] == table and str(meta["row_dict"].get(br_column) or "").strip() == code:
                    meta["row_dict"]["Company_Stock"] = quantity
                    if "Company_Stock" in self.tree["columns"]:
                        self.tree.set(item, "Company_Stock", quantity)
            self.company_stock_var.set(f"{quantity:g}")
            self.status_var.set(f"{code}: local stock updated ({quantity:g}).")
        except Exception as exc:
            log.exception("Selected stock update failed")
            self.status_var.set(f"{code}: could not save stock: {exc}")

    # ========================================================
    # FOOTPRINT SUGGESTION
    # ========================================================

    def on_footprints_label_clicked(self, event=None):
        """Suggest a footprint: installed KiCad library first, web search only if needed.

        A Description such as "RES SMD 10K 0603" or a Package field resolves
        directly against the installed KiCad libraries. Only when that finds
        nothing is the MPN searched online (see footprint_suggestions).
        """
        mpn = get_entry_value_by_normalized_name(self.entry_widgets, "Manufacturer_Part_Number", "Manufacturer Part Number", "MPN")
        package = get_entry_value_by_normalized_name(self.entry_widgets, "Package", "Package / Case", "Supplier Device Package")
        description = get_entry_value_by_normalized_name(self.entry_widgets, "Description", "Detailed_Description", "Detailed Description")
        manufacturer = get_entry_value_by_normalized_name(self.entry_widgets, "Manufacturer", "Mfr", "Manufacturer_Name")
        current_footprint = get_entry_value_by_normalized_name(self.entry_widgets, "Footprints", "Footprint")
        if not mpn and not package and not description and not current_footprint:
            messagebox.showinfo('Footprint suggestions', 'Enter a manufacturer part number, package or description to get suggestions.')
            return
        from footprint_suggestions import suggest
        from ui.footprint_window import show_suggestions
        context = (self.current_table, self.selected_key_column, self.selected_key_value, mpn)

        def apply_suggestion(value):
            current_mpn = get_entry_value_by_normalized_name(self.entry_widgets, "Manufacturer_Part_Number", "Manufacturer Part Number", "MPN")
            if context != (self.current_table, self.selected_key_column, self.selected_key_value, current_mpn):
                messagebox.showinfo("Footprint", 'The selected part changed. Request suggestions again for the new part.', parent=self.root)
                return False
            for col, widget in self.entry_widgets.items():
                if normalize_key(col) in ("footprints", "footprint"):
                    widget_set_value(widget, value)
                    self.status_var.set(f"Footprint applied to form: {value}")
                    return True
            return False

        def start(extra_root=None):
            if extra_root:
                self._footprint_library_root = extra_root
            root = getattr(self, "_footprint_library_root", None)
            def worker(report, is_cancelled):
                return suggest(mpn, package, context[0], root, report, is_cancelled,
                               description=description, manufacturer=manufacturer,
                               current_footprint=current_footprint)
            def completed(result):
                self.status_var.set('Footprint suggested from the installed KiCad library.' if result.get("local")
                                    else f"Footprint suggestions: {len(result['candidates'])} candidates.")
                show_suggestions(self.root, mpn or description, result, apply_suggestion, start)
            self.run_in_background(worker, completed,
                lambda exc: messagebox.showerror('Footprint suggestion', str(exc), parent=self.root),
                busy_message='Checking the KiCad library and, if needed, the web...')
        start()

    # ========================================================
    # EXPORT CMP CODES
    # ========================================================

    # ========================================================
    # BOM / SEARCH / DIGIKEY
    # ========================================================

    def open_product_tree(self):
        from ui.product_tree_window import ProductTreeWindow
        window = getattr(self, "product_tree_window", None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_set()
            return
        try:
            self.product_tree_window = ProductTreeWindow(self.root)
        except Exception as exc:
            messagebox.showerror("Product Tree", str(exc), parent=self.root)

    def open_obsolete_products(self):
        from ui.obsolete_products_window import ObsoleteProductsWindow
        window = getattr(self, 'obsolete_products_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_set()
            return
        try:
            self.obsolete_products_window = ObsoleteProductsWindow(self.root)
        except Exception as exc:
            messagebox.showerror('Obsolete Products', str(exc), parent=self.root)

    def select_bom_file(self):
        file_path = filedialog.askopenfilename(title='Select a BOM Excel file', filetypes=[("Excel Files", "*.xlsx *.xls"), ("Excel Workbook", "*.xlsx"), ("Excel 97-2003", "*.xls")])
        if not file_path:
            return
        try:
            if file_path.lower().endswith(".xlsx"):
                pd.read_excel(file_path, engine="openpyxl", nrows=1)
            elif file_path.lower().endswith(".xls"):
                pd.read_excel(file_path, engine="xlrd", nrows=1)
            else:
                messagebox.showwarning('Invalid file', 'Select an .xlsx or .xls Excel file.')
                return
            self.bom_file_path = file_path
            self.bom_file_label_var.set(os.path.basename(file_path))
            self.process_bom_list_with_digikey()
        except Exception as e:
            self.bom_file_path = None
            self.bom_file_label_var.set("")
            messagebox.showerror('BOM file error', f"Could not read the Excel file:\n{e}")

    def reset_bom_selection(self):
        self.bom_file_path = None
        self.bom_file_label_var.set("")

    def read_bom_dataframe(self):
        """Read the selected BOM into a DataFrame, or raise ValueError with a message for the user."""
        path = self.bom_file_path
        if path.lower().endswith(".xlsx"):
            df = pd.read_excel(path, engine="openpyxl")
        elif path.lower().endswith(".xls"):
            df = pd.read_excel(path, engine="xlrd")
        else:
            raise ValueError('The BOM file must be .xlsx or .xls.')
        if df.empty:
            raise ValueError('The selected BOM file contains no data.')
        return df

    def bom_output_path(self, suffix):
        folder = os.path.dirname(self.bom_file_path)
        base = os.path.splitext(os.path.basename(self.bom_file_path))[0]
        return os.path.join(folder, f"{base}_{suffix}.xlsx")

    def _run_bom_job(self, supplier, extra_columns, fill_row, suffix):
        """Shared BOM driver for both suppliers; the row loop runs off the UI thread."""
        if not self.bom_file_path:
            messagebox.showwarning("No BOM selected", 'Select an Excel file using Check BOM List first.')
            return
        try:
            df = self.read_bom_dataframe()
        except ValueError as e:
            messagebox.showwarning('Invalid file', str(e))
            return
        except Exception as e:
            messagebox.showerror('BOM processing error', f"Could not read the BOM list:\n{e}")
            return

        part_col = find_bom_part_number_column(df)
        if not part_col:
            messagebox.showerror(
                'Column not found',
                f"In the BOM file, {supplier} search requires a manufacturer part number column."
            )
            return

        for col in ["price", "part status", "In-Stock"] + list(extra_columns):
            # Clear stale supplier data and accept numeric API values on pandas 3.
            df[col] = pd.Series("", index=df.index, dtype=object)

        output = self.bom_output_path(suffix)
        br_column = prepare_stock_column(df)
        total = len(df)

        def worker(report, is_cancelled):
            processed = errors = 0
            cancelled = False
            supplier_unreachable = False
            network_failures = 0
            stock_cache = {}
            for position, (index, row) in enumerate(df.iterrows(), start=1):
                if is_cancelled():
                    cancelled = True
                    break
                pn = row.get(part_col, "")
                pn = "" if pd.isna(pn) else str(pn).strip()
                report(f"{supplier} BOM lookup: {position}/{total}" + (f" - {pn}" if pn else ""), position)
                fill_stock_row(df, index, br_column, stock_cache)
                if not pn:
                    df.at[index, "part status"] = 'Manufacturer part number missing'
                    continue
                if supplier_unreachable:
                    df.at[index, "part status"] = f"Error: {supplier} unreachable"
                    errors += 1
                    continue
                try:
                    fill_row(df, index, pn)
                    processed += 1
                    network_failures = 0
                except (requests.ConnectionError, requests.Timeout) as e:
                    df.at[index, "part status"] = f"Error: {e}"
                    errors += 1
                    network_failures += 1
                    # Circuit breaker: a few consecutive connection failures mean
                    # the supplier (or the network) is down. Stop querying so the
                    # remaining rows do not each wait out the full timeouts.
                    if network_failures >= SUPPLIER_FAILURE_LIMIT:
                        supplier_unreachable = True
                except Exception as e:
                    df.at[index, "part status"] = f"Error: {e}"
                    errors += 1
            export_names = {
                "In-Stock": "Digikey Stock", "price": "Price", "part status": "Part Status",
                **{name: name.replace('Adet', "pices") for name in PRICE_QUANTITY_COLUMNS},
            }
            # A previously exported BOM may already contain the new headings.
            # Keep the freshly queried columns instead of producing duplicates.
            duplicates = [new for old, new in export_names.items()
                          if old in df.columns and new in df.columns]
            df.drop(columns=duplicates).rename(columns=export_names).to_excel(
                output, index=False, engine="openpyxl")
            return {"processed": processed, "errors": errors, "cancelled": cancelled,
                    "supplier_unreachable": supplier_unreachable,
                    "stock_unreachable": stock_server_unreachable(stock_cache)}

        def on_success(result):
            state = 'cancelled' if result["cancelled"] else 'completed'
            processed = result["processed"]
            errors = result["errors"]
            warnings = []
            if result.get("supplier_unreachable"):
                warnings.append(f"{supplier} could not be reached after {SUPPLIER_FAILURE_LIMIT} consecutive "
                                "connection failures; the remaining rows were not queried.")
            if result.get("stock_unreachable"):
                warnings.append("The local stock server could not be reached; stock quantities were not filled.")
            self.status_var.set(f"BOM {supplier} operation {state}. Processed: {processed}, Error: {errors}"
                                + (" (connection problems, see message)" if warnings else ""))
            show = messagebox.showwarning if warnings else messagebox.showinfo
            show(
                'BOM list updated',
                f"BOM list {state} with {supplier} data.\n\n"
                f"Processed: {processed}\nError: {errors}\n\n"
                + ("".join(f"Warning: {w}\n\n" for w in warnings))
                + f"Saved file:\n{output}"
            )
            self.reset_bom_selection()

        def on_error(exc):
            messagebox.showerror('BOM processing error', f"Failed to process the BOM list:\n{exc}")
            self.status_var.set('BOM processing failed.')

        self.run_in_background(
            worker, on_success, on_error,
            total=total, busy_message=f"{supplier} BOM lookup started..."
        )

    def process_bom_list_with_digikey(self):
        def fill_row(df, index, pn):
            product = get_digikey_part_data(pn)
            if not product:
                df.at[index, "part status"] = "Not Found"
                return
            extracted = extract_digikey_fields(product)
            df.at[index, "price"] = get_digikey_price(product)
            df.at[index, "part status"] = get_part_status_from_digikey(product, extracted)
            df.at[index, "In-Stock"] = get_digikey_stock(product)
            for col, qty in PRICE_QUANTITY_COLUMNS.items():
                df.at[index, col] = get_digikey_price_for_quantity(product, qty)

        self._run_bom_job("DigiKey", PRICE_QUANTITY_COLUMNS.keys(), fill_row, "DigiKey_Updated")

    def get_search_part_number(self, supplier):
        """Search box first, then the form's Manufacturer Part Number field."""
        text = self.search_var.get().strip()
        if not text:
            text = get_entry_value_by_normalized_name(
                self.entry_widgets,
                "Manufacturer_Part_Number",
                "Manufacturer Part Number",
                "ManufacturerPartNumber",
                "Mfr Part Number",
                "Mfr_Part_Number",
                "MfrPartNumber",
                "MPN",
                "Part Number",
                "Part_Number",
            )
        if not text:
            messagebox.showwarning(
                'Warning',
                f"{supplier} search requires a part number in the search box or "
                'Manufacturer Part Number field.'
            )
            return ""
        return text

    def digikey_search_records(self):
        if self.bom_file_path:
            self.process_bom_list_with_digikey()
            return
        text = self.get_search_part_number("DigiKey")
        if not text:
            return

        def worker(report, is_cancelled):
            return get_digikey_part_data(text)

        def on_success(product):
            if not product:
                self.clear_digikey_side_info()
                self.status_var.set('No DigiKey results.')
                messagebox.showinfo('No results', 'No DigiKey results found for this part number.')
                return
            extracted = extract_digikey_fields(product)
            status = get_part_status_from_digikey(product, extracted)
            self.update_digikey_side_info(product)
            self.fill_form_from_digikey_data(product)
            if status and status.strip().lower() != "active":
                messagebox.showwarning(
                    'DigiKey part status warning',
                    f"This component is not Active on DigiKey.\n\nPart status: {status}"
                )

        def on_error(exc):
            if isinstance(exc, ValueError):
                messagebox.showwarning("Missing settings", str(exc))
            else:
                messagebox.showerror('Error', f"DigiKey search failed:\n{exc}")
            self.status_var.set('DigiKey search failed.')

        self.run_in_background(
            worker, on_success, on_error,
            busy_message=f"Searching DigiKey for: {text}"
        )

    def lcsc_search_records(self):
        if self.is_busy():
            messagebox.showinfo('LCSC Manager', 'Wait for the current operation to finish.', parent=self.root)
            return
        existing = getattr(self, '_lcsc_manager_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        from ui.lcsc_manager_window import LcscManagerWindow
        query = self.search_var.get().strip() or get_entry_value_by_normalized_name(
            self.entry_widgets, 'Manufacturer_Part_Number', 'MPN')

        def apply(product, library_fields):
            self.fill_form_from_lcsc_data(product)
            for column, widget in self.entry_widgets.items():
                for name, value in library_fields.items():
                    if normalize_key(column) == normalize_key(name):
                        widget_set_value(widget, value)
            self.stock_qty_var.set(get_lcsc_stock(product) or '-')
            self.unit_price_var.set(get_lcsc_price(product) or '-')
            self.product_status_var.set(get_lcsc_part_status(product) or 'Unknown')

        self._lcsc_manager_window = LcscManagerWindow(self.root, apply, query)

    def fill_form_from_lcsc_data(self, product):
        extracted = extract_lcsc_fields(product)
        filled = 0
        for column, widget in self.entry_widgets.items():
            key = normalize_key(column)
            if key in {'id', 'companypartnumber', 'companystock', 'verified', 'symbols', 'footprints'} or is_audit_column(column):
                continue
            if key == 'reference':
                if is_blank(widget.get().strip()):
                    widget_set_value(widget, get_default_reference_for_table(self.current_table))
                continue
            value = map_lcsc_value_to_form_column(column, extracted, self.current_table)
            if value not in (None, ''):
                widget_set_value(widget, value)
                filled += 1
        self.status_var.set(f'LCSC Manager: {filled} fields loaded. Use Add or Update to save the component.')

    def fill_form_from_digikey_data(self, product):
        extracted = extract_digikey_fields(product)
        filled = empty = 0
        for col, widget in self.entry_widgets.items():
            if col.lower() == "id" or is_audit_column(col):
                continue
            current = widget.get().strip()
            if normalize_key(col) == "reference" and is_blank(current):
                ref_value = get_default_reference_for_table(self.current_table)
                if ref_value:
                    widget_set_value(widget, ref_value)
                    filled += 1
                    continue
            if col == "Company_Part_Number":
                if current.startswith("CMP-"):
                    filled += 1
                    continue
                if is_blank(current):
                    widget_set_value(widget, "-")
                    filled += 1
                    continue
            matched = map_digikey_value_to_form_column(col, extracted, self.current_table)
            if matched not in [None, ""]:
                widget_set_value(widget, matched)
                filled += 1
            else:
                empty += 1
        self.status_var.set(f"DigiKey data loaded into the record form. Fields filled: {filled}, empty fields: {empty}")

# ============================================================
# RUN
# ============================================================

def setup_logging():
    """Log file next to the exe/script.

    Holds program start/exit, sign-ins, every record change (logger
    "company.audit") and unhandled errors. The frozen build runs windowless
    (console=False), so without this an exception during startup simply closed
    the program with no trace. When the file reaches 5 MB it is renamed to
    Company_Kicad_DB.log.1 (up to .5) and a new one is started; the full
    change history also stays in the database's Change_Log table.
    """
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except Exception:
        pass


def report_fatal_error(exc_type, exc_value, exc_tb):
    """sys.excepthook / Tk report_callback_exception replacement."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.error("unhandled exception:\n%s", text)
    try:
        messagebox.showerror(
            "Unexpected error",
            f"{exc_type.__name__}: {exc_value}\n\nDetails were written to the log file:\n{LOG_FILE}",
        )
    except Exception:
        pass


def main():
    setup_logging()
    log.info("start: app_dir=%s db=%s", APP_DIR, DB_FILE)
    sys.excepthook = report_fatal_error
    root = tk.Tk()
    root.withdraw()
    # Exceptions raised inside Tk callbacks (button handlers, after() jobs) do
    # not reach sys.excepthook; Tk routes them here instead.
    root.report_callback_exception = report_fatal_error
    try:
        # Before anything writes to the database (the login flow creates the
        # Users table on first run).
        backup_result = App.take_daily_backup()
        user = sign_in(root)
        if not user:
            log.info("exit: giris yapilmadi")
            root.destroy()
            return
        app = App(root, user=user, backup_result=backup_result)
    except Exception:
        report_fatal_error(*sys.exc_info())
        raise
    root.deiconify()
    root.lift()
    root.mainloop()
    log.info("exit user=%s", user.get("Username"))


def sign_in(root):
    """Show the login dialogs; returns the user dict or None if cancelled."""
    from ui.login_window import run_login_flow
    settings = load_settings()
    # The dialogs load the logo themselves (ui/logo.py) and show it top-centre.
    user = run_login_flow(root, initial_username=str(settings.get("last_username") or ""))
    if user:
        try:
            settings = load_settings()
            settings["last_username"] = user["Username"]
            save_settings(settings)
        except Exception:
            log.exception('Could not save last_username')
    return user


if __name__ == "__main__":
    main()
