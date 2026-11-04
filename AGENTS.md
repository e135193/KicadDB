# Repository Guidelines

## Project Overview
Python/Tkinter desktop app for KiCad component & BOM management. Main entry: `Company_Kicad_DB.py` (`App` class). UI dialogs in `ui/`. Core modules:
- `config.py` — frozen-aware path resolution (database, symbols, library root)
- `db.py` — SQLite CRUD, audit logging, permissions, schema migrations
- `product_tree.py` — Project/Subproject/Card/Revision hierarchy, BOM persistence, permissions
- `project_tree_import.py` — Excel workbook import with column mapping
- `product_tree_export.py` — Consolidated Excel/CSV export
- `project_overview.py` — Multi-project material summary
- `lcsc_*.py`, `step_*.py` — LCSC/JLCPCB integration, 3D STEP preview

## Build, Test, Run Commands
```powershell
# Setup (Python 3.11+)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Run app
python Company_Kicad_DB.py

# Tests (use temp SQLite, mocks; no live DB/supplier calls)
python -m unittest discover -s tests -v
python -m unittest tests.test_product_tree -v      # single module
python run_smoke.py                                # GUI smoke test

# Build executable
python -m PyInstaller Company_Kicad_DB.spec
# Output: dist/Company_Kicad_DB.exe
```

## Key Architectural Rules
- **Threading**: All DB/network work in background workers (`threading.Thread` + `queue.Queue`). Tk widgets updated **only on main thread** (via `root.after` or `queue` polling).
- **Database**: Route all writes through `db.py`. Use `db.connection()` context manager. Bind SQL values; quote identifiers with `db.quote_identifier()`.
- **Permissions**: `db.current_user_is_admin(conn)` / `current_user_is_active(conn)` — resolve from DB, never from UI state. Verified components editable only by admins.
- **Config**: `settings.json` holds paths, API keys (encrypted via Windows DPAPI in `api_keys.py`). Never commit `settings.json`, `KicadDB.sqlite`, `API-Keys.json`, logs, or caches.

## Product Tree & BOM Rules (Critical)
Hierarchy: **Project → Subproject → Card → Revision**. Codes unique per parent. IDs preserved.
- **Revisions shared** across projects; quantities project-local.
- **Add Card Revision** → creates Draft. **Add BOM** targets selected revision.
- **Import**: atomic validation, rejects stale previews, preserves conflicting BOMs.
- **Sync Database**: fills missing CMPs from live matches or unique exact MPN+Manufacturer. Preserves existing CMPs; skips ambiguity.
- **Approval**: Users add/edit/delete Drafts. **Only admins** modify/delete Approved BOMs, approve, withdraw approval, delete projects. Edits preserve approval metadata; only "Return to Draft" withdraws approval.
- **Export**: defaults to current revisions; project totals multiply qty × board count.
- **Project Overview**: highest linked revision per card/project (letter `-B > -A > -X10 > -X2 > -X1`). Critical materials: IC, MCU, Mosfet only.
- **Tree labels** from project-code suffix: `-A`/`-B` → Production; `-X`/`-X1`/`-X2` → Development. Do not alter stored codes.

## LCSC Manager (Pinned Core)
- Uses `third_party/lcsc_manager/` via `lcsc_api.py` + `ui/lcsc_manager_window.py`. No HTML scraping.
- Preserve supplier IDs, internal BR/stock/verification fields, unknown lifecycle status.
- `lcsc_library.py`: stages conversion/registration, preserves existing files, rolls back failed commits. No placeholder CAD.
- `lcsc_preview.py`: read-only 2D views from converted CAD. Background workers (`ui/cad_preview.py`). Stale/closed windows ignored.
- Download CAD: saves directly to selected Library folder (no subfolders), KiCad registration disabled, no form callback. Staging uses temp; replacements backed up as `.bak`. Metadata transfer separate.
- STEP preview: `step_preview.py` reads real geometry via pinned `cadquery-ocp` (AP242 > AP214 > AP203). `ui/step_preview.py`: rotate/pan/zoom, local file open. Preview downloads temp, no library population. Bundle CAD DLLs + licenses in EXE.

## Testing Conventions
- `unittest`, `test_*.py`, `test_*` methods.
- Temp SQLite fixtures via `tempfile.TemporaryDirectory()` + `patch.object(db, "get_connection", ...)`
- Mock external APIs (`requests`, `digikey_api`).
- Cover: permissions, shared revisions, rollback, audit trail.
- `gc.collect()` in `tearDown` to clean Tk roots before next test spawns workers.

## Common Gotchas
- `config.py` must stay import-cycle free — uses `_setting()` to re-read `settings.json` inline.
- `db.py` audit columns (`Created_At`, `Created_By`, `Modified_At`, `Modified_By`) written only by `db.py`; form values ignored.
- `SUPPLIER_FAILURE_LIMIT = 3` circuit breaker in `Company_Kicad_DB.py` for BOM jobs.
- Component tables: Resistor, Capacitor, IC, MCU, Mosfet, Inductor, Button_Switch, Header, PAD, Miscellaneous, Mechanical + `SYSTEM_TABLES` excluded from listings.
- `settings.json` `tree_columns_by_table` defines per-table column visibility/order — update both default and per-table sections when adding columns.

## Files to Never Commit
- `settings.json`, `KicadDB.sqlite`, `API-Keys.json`, `*.log`, `__pycache__/`, `build/`, `dist/`, `graphify-out/`, `.venv/`