"""Live local inventory enrichment for BOM Excel outputs."""
import pandas as pd
from db import get_column_name_by_alias
from local_stock import BR_ALIASES, fetch_selected_stock

COLUMN = "Company Stock QTY"
NO_CODE = 'No CMP code'
NOT_FOUND = 'Not found'
LOOKUP_FAILED = 'Lookup failed'
SERVER_UNREACHABLE = 'Stock server unreachable'

# Key stored in the per-job cache once the stock server could not be reached.
# Every later row is marked without another network round trip, so an
# offline server costs one connection timeout per BOM instead of one per code.
UNREACHABLE_KEY = "__unreachable__"


def prepare_stock_column(df):
    if "In-Stock" not in df.columns:
        df["In-Stock"] = ""
    if COLUMN in df.columns:
        df.drop(columns=[COLUMN], inplace=True)
    df.insert(df.columns.get_loc("In-Stock") + 1, COLUMN,
              pd.Series("", index=df.index, dtype=object))
    return get_column_name_by_alias(df.columns, BR_ALIASES + ["BPN", "BPM"])


def stock_server_unreachable(cache):
    return bool(cache.get(UNREACHABLE_KEY))


def fill_stock_row(df, index, br_column, cache):
    value = df.at[index, br_column] if br_column else None
    code = "" if pd.isna(value) else str(value).strip()
    if not code or code == "-":
        df.at[index, COLUMN] = NO_CODE
        return
    if stock_server_unreachable(cache):
        df.at[index, COLUMN] = SERVER_UNREACHABLE
        return
    if code not in cache:
        try:
            quantity = fetch_selected_stock(code)
            cache[code] = NOT_FOUND if quantity is None else quantity
        except (ConnectionError, TimeoutError):
            cache[UNREACHABLE_KEY] = True
            df.at[index, COLUMN] = SERVER_UNREACHABLE
            return
        except Exception:
            cache[code] = LOOKUP_FAILED
    df.at[index, COLUMN] = cache[code]
