# Brensan integration of KiCad LCSC Manager

Source: https://github.com/hulryung/kicad-lcsc-manager

Pinned commit: `741ee3d4d3e6bcfaf2df3c64bce8c2ced463a59b` (package version 0.10.0).
Retrieved 2026-11-02. This directory includes the API client, conversion engines,
symbol/library-table helpers and vendored easyeda2kicad sources required by them.
No KiCad plugin installer, wxPython UI, dependency downloader or auto-update code runs.

Host changes:

- `__init__.py` omits KiCad plugin registration and dependency path injection.
- `utils/logger.py` uses Brensan logging without creating separate files.
- `utils/config.py` provides defaults without changing the user's plugin settings.
- `converters/symbol_converter.py` raises conversion errors instead of generating
  placeholder symbols. The Brensan importer also omits placeholder 3D models.
- `api/lcsc_api.py` preserves typed API errors in keyword searches.

`lcsc_api.py`, `lcsc_library.py` and `ui/lcsc_manager_window.py` in the application
provide request accounting, field mapping, staged file import and the Tkinter UI.
The API endpoints are reverse-engineered by upstream and may change.

Keep `LICENSE`, `NOTICE.md`, `converters/jlc2kicad/LICENSE` and
`vendor/easyeda2kicad/LICENSE` with redistributed copies. The main project and
JLC2KiCad handlers retain their MIT notices; the vendored easyeda2kicad code retains
its AGPL-3.0 license. This source directory is included in packaged distributions.
