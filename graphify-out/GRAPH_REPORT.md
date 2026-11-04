# Graph Report - kicad DB  (2026-11-03)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2222 nodes · 6469 edges · 97 communities (71 shown, 26 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 453 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e64a3435`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- db.py
- Brensan KiCad Database Library - Kullanici Kitapcigi (v2, 19 Oct 2026, 31 pages)
- TestProductTree
- product_tree.py
- CLAUDE.md project guidance
- build_export
- LCSCAPIClient
- login_window.py
- local_stock.py
- Brensan_Kicad_DB.py
- Brensan KiCad DB README (English Product Tree notes + Turkish manual)
- App
- connection
- users.py
- .__init__
- product_tree_window.py
- test_formatting.py
- easyeda_importer.py
- LCSC Manager integration (docs)
- export_kicad_footprint.py
- json
- project_tree_import.py
- config.py
- backup_database
- .run
- ApiKeyManager
- lcsc_preview.py
- EasyedaApi
- api/lcsc_api.py
- Update Brensan Stock page
- symbol_lib.py
- Model3DConverter
- symbol_handlers.py
- import_component
- LcscManagerWindow
- ._run_bom_job
- normalize_key
- ProductTreeWindow
- _safe_float
- reset_mail.py
- lcsc_library.py
- Purchasing.py
- Brensan Component Entry Skill
- build_overview
- HistoryTab
- BomEditWindow
- component_tool.py
- password_reset.py
- folder
- .test_window_add_card_revision_from_card_and_revision_selection
- footprint_suggestions.py
- .select_revision
- save_project
- _attempt_search_with_credentials
- Record form (auto-generated from the selected table's columns)
- SymbolConverter
- convert_fields_to_types
- UsersTab
- complete_reset
- FootprintConverter
- lib_table.py
- TestLocalSuggestions
- re
- authenticate
- .refresh_tree
- bom_edit_snapshot
- EeSymbol
- rank_candidates
- suggest
- .test_other_errors_still_rotate_to_the_next_key
- Any
- .loaded
- online_evidence
- ProjectOverviewPanel
- Desktop password reset (docs)
- _ResetHandler
- main
- hash_password
- UsersTestCase
- build_main_ui
- extract_kicad_symbols_from_file
- ._aliased
- TestSelectedStockDisplay
- library_roots
- .pick_component
- descendants

## God Nodes (most connected - your core abstractions)
1. `Brensan KiCad Database Library - Kullanici Kitapcigi (v2, 19 Oct 2026, 31 pages)` - 136 edges
2. `connection()` - 120 edges
3. `App` - 94 edges
4. `TestProductTree` - 84 edges
5. `ProductTreeWindow` - 81 edges
6. `save_bom()` - 81 edges
7. `set_current_user()` - 76 edges
8. `list_projects()` - 48 edges
9. `Brensan KiCad DB README (English Product Tree notes + Turkish manual)` - 47 edges
10. `CLAUDE.md project guidance` - 46 edges

## Surprising Connections (you probably didn't know these)
- `New card / Link saved card / Add Card Revision` --semantically_similar_to--> `Add Card Revision page`  [INFERRED] [semantically similar]
  README.md → docs/Brensan_KiCad_DB_Kullanici_Kitapcigi.html
- `Project overview: latest linked revision per card` --semantically_similar_to--> `Project overview counters and Recommendations`  [INFERRED] [semantically similar]
  README.md → docs/Brensan_KiCad_DB_Kullanici_Kitapcigi.html
- `Product Tree: Top-level project -> Subproject -> Board -> Revision` --semantically_similar_to--> `Product Tree toolbar and navigation`  [INFERRED] [semantically similar]
  README.md → docs/Brensan_KiCad_DB_Kullanici_Kitapcigi.html
- `Common component columns (Reference, Value, Symbols, Footprints, ...)` --semantically_similar_to--> `Verified / Comment / Alternate Part Number / Brensan_Stock / Product Status / Created-Modified`  [INFERRED] [semantically similar]
  .claude/skills/brensan-component-entry/references/tables.md → docs/Brensan_KiCad_DB_Kullanici_Kitapcigi.html
- `Login, roles (admin/user) and user management` --semantically_similar_to--> `Users and permission limits page`  [INFERRED] [semantically similar]
  README.md → docs/Brensan_KiCad_DB_Kullanici_Kitapcigi.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Product Tree BOM lifecycle (New card -> Add Card Revision -> Add BOM -> Edit BOM / Update match -> Sync Database -> Approve BOM / Return to Draft -> Export BOM)** — docs_brensan_kicad_db_kullanici_kitapcigi_new_card_window, docs_brensan_kicad_db_kullanici_kitapcigi_add_card_revision, docs_brensan_kicad_db_kullanici_kitapcigi_add_bom, docs_brensan_kicad_db_kullanici_kitapcigi_edit_bom_window, docs_brensan_kicad_db_kullanici_kitapcigi_update_match_window, docs_brensan_kicad_db_kullanici_kitapcigi_sync_database, docs_brensan_kicad_db_kullanici_kitapcigi_approve_bom, docs_brensan_kicad_db_kullanici_kitapcigi_return_to_draft, docs_brensan_kicad_db_kullanici_kitapcigi_export_bom_window [EXTRACTED 1.00]
- **BR code round trip (Export UD BR -> Part Number Request -> stock system -> Update Brensan Stock -> Sync Database -> BOM lines)** — docs_brensan_kicad_db_kullanici_kitapcigi_export_ud_br, docs_brensan_kicad_db_kullanici_kitapcigi_part_number_request_template, docs_brensan_kicad_db_kullanici_kitapcigi_local_stock_server, docs_brensan_kicad_db_kullanici_kitapcigi_update_brensan_stock, docs_brensan_kicad_db_kullanici_kitapcigi_sync_database, docs_brensan_kicad_db_kullanici_kitapcigi_brensan_part_number [EXTRACTED 1.00]
- **Component definition workflow (Record form -> Symbols / Footprints / Datasheet -> Save -> DigiKey verify)** — docs_brensan_kicad_db_kullanici_kitapcigi_record_form, docs_brensan_kicad_db_kullanici_kitapcigi_symbol_suggestions_window, docs_brensan_kicad_db_kullanici_kitapcigi_footprint_suggestions_window, docs_brensan_kicad_db_kullanici_kitapcigi_datasheet_label, docs_brensan_kicad_db_kullanici_kitapcigi_save_button, docs_brensan_kicad_db_kullanici_kitapcigi_digikey_lookup, docs_brensan_kicad_db_kullanici_kitapcigi_verified_field [EXTRACTED 1.00]
- **LCSC Manager integration stack (docs, pinned upstream, vendored licenses, fixtures, glue)** — docs_lcsc_manager_lcsc_manager_integration, third_party_lcsc_manager_readme_brensan_lcsc_manager, third_party_lcsc_manager_readme_brensan_application_glue_modules, third_party_lcsc_manager_notice_easyeda2kicad, third_party_lcsc_manager_notice_jlc2kicad_lib, tests_fixtures_lcsc_manager_readme_c25804_fixtures, agents_lcsc_manager [EXTRACTED 1.00]
- **Audit trail and permission enforcement in db.py** — claude_audited_component_writes, claude_verified_permission_rule, claude_connection_context_manager, readme_change_log_table, readme_audit_columns, readme_app_log_file, docs_brensan_kicad_db_kullanici_kitapcigi_approval_vs_verified, _claude_skills_brensan_component_entry_skill_data_layer_only_writes [INFERRED 0.85]
- **BR number lifecycle: Export UD BR -> purchasing -> Update Brensan Stock -> Sync Database** — readme_export_ud_br, readme_update_brensan_stock, readme_sync_database, docs_brensan_kicad_db_kullanici_kitapcigi_br_code_return_path, claude_sync_database, _claude_skills_brensan_component_entry_skill_dry_run_then_commit [INFERRED 0.85]

## Communities (97 total, 26 thin omitted)

### Community 0 - "db.py"
Cohesion: 0.05
Nodes (45): _apply_audit_values(), _check_verified_write(), delete_data(), ensure_change_log_table(), ensure_extra_columns_for_all_tables(), ensure_extra_columns_for_table(), _fetch_rows(), fill_missing_brensan_part_number() (+37 more)

### Community 1 - "Brensan KiCad Database Library - Kullanici Kitapcigi (v2, 19 Oct 2026, 31 pages)"
Cohesion: 0.08
Nodes (73): Active User role, Add BOM (import BOM_Template.xlsx workbook into the selected card revision), Add Card Revision page, Administrator role, Alternate Part Number field, DigiKey key rotation and quota (daily limit or 401/403/429 -> next key, blocked key shown; usage counts real HTTP requests, resets per UTC day), API-Keys.json (real credentials; never share in manuals, screen shares or support logs), Approve BOM (admin; empty BOM cannot be approved; approver and time stored on revision and in change history; applies to all projects using the revision) (+65 more)

### Community 2 - "TestProductTree"
Cohesion: 0.11
Nodes (16): approve_bom(), attach_revision(), bom_permissions(), detach_project_item(), difference(), list_projects(), project_tree(), rematch_line() (+8 more)

### Community 3 - "product_tree.py"
Cohesion: 0.06
Nodes (40): current_user_is_active(), current_user_is_admin(), add_card_revision(), _add_draft_revision(), can_approve_bom(), can_delete_project_item(), can_edit_card(), create_card() (+32 more)

### Community 4 - "CLAUDE.md project guidance"
Cohesion: 0.06
Nodes (36): Category-specific tables and columns, Coding style: English UI, background workers, main-thread Tk, Product Tree & BOM Rules, Repository Guidelines (AGENTS.md), Sync Database and usage views, Brensan_Kicad_DB.py monolith and module map, Brensan KiCad component database app (Tkinter, SQLite, ODBC), CLAUDE.md project guidance (+28 more)

### Community 5 - "build_export"
Cohesion: 0.08
Nodes (11): _bom_record(), build_export(), _cards(), consolidate(), revision_choices(), _scope(), write_export(), link_card_revision() (+3 more)

### Community 6 - "LCSCAPIClient"
Cohesion: 0.08
Nodes (5): LcscAPI, get_api_client(), LCSCAPIClient, LCSCAPIError, LCSCRateLimitError

### Community 7 - "login_window.py"
Cohesion: 0.08
Nodes (10): center_window(), ChangePasswordDialog, _Dialog, FirstAdminDialog, ForgotPasswordDialog, worker(), LoginDialog, run_login_flow() (+2 more)

### Community 8 - "local_stock.py"
Cohesion: 0.09
Nodes (18): fill_stock_row(), prepare_stock_column(), stock_server_unreachable(), worker(), AmbiguousStockResult, fetch_missing_part(), fetch_page(), fetch_selected_stock() (+10 more)

### Community 9 - "Brensan_Kicad_DB.py"
Cohesion: 0.12
Nodes (27): on_success(), fill_row(), bool_to_db(), get_digikey_part_data(), cmd_lookup(), get_connection(), add_common_aliases(), add_result() (+19 more)

### Community 10 - "Brensan KiCad DB README (English Product Tree notes + Turkish manual)"
Cohesion: 0.07
Nodes (38): product_tree_export read-only consolidated exports, Product Tree permission/sharing rules pinned by tests, Project Import validates whole workbook, rejects stale previews, Revision ordering -B > -A > -X10 > -X2 > -X1, Add BOM page (BOM_Template.xlsx columns, matching order BR then manufacturer+MPN), Approve BOM, Return to Draft, Delete by selected level, New card / Link saved card / Edit card, Compare revisions page (+30 more)

### Community 11 - "App"
Cohesion: 0.07
Nodes (8): App, completed(), poll(), retry(), completed(), db_to_bool(), widget_clear(), _widget_state()

### Community 13 - "connection"
Cohesion: 0.10
Nodes (9): connection(), set_current_user(), component_catalog(), ensure_schema(), obsolete_products(), TestMissingBrCodes, lookup(), lookup() (+1 more)

### Community 14 - "users.py"
Cohesion: 0.15
Nodes (22): TestEmailAndDefaults, TestAccountRules, _active_admin_count(), change_own_password(), _check_email_available(), count_users(), create_user(), delete_user() (+14 more)

### Community 15 - ".__init__"
Cohesion: 0.09
Nodes (10): create_default_footprint_json(), ensure_settings_file(), get_default_settings(), load_footprint_options(), load_settings(), normalize_footprint_list(), normalize_tree_columns(), normalize_tree_sort() (+2 more)

### Community 16 - "product_tree_window.py"
Cohesion: 0.09
Nodes (7): ObsoleteProductsWindow, copy_selected_rows(), visit(), enable_copy(), select_all(), visit(), table()

### Community 17 - "test_formatting.py"
Cohesion: 0.10
Nodes (13): build_tree_columns_settings(), find_bom_part_number_column(), format_capacitor_value_for_kicad(), format_inductor_value_for_kicad(), format_resistor_value_for_kicad(), is_missing_brensan_part_number(), normalize_tree_columns_by_table(), parse_tree_columns_settings() (+5 more)

### Community 18 - "easyeda_importer.py"
Cohesion: 0.16
Nodes (24): add_easyeda_arc(), add_easyeda_pin(), EasyedaFootprintImporter, EasyedaPinType, EeFootprint, EeFootprintArc, EeFootprintBbox, EeFootprintCircle (+16 more)

### Community 19 - "LCSC Manager integration (docs)"
Cohesion: 0.10
Nodes (27): LCSC Manager (pinned core, no HTML scraping), API-Keys.json + ApiKeyManager daily-usage rotation, DigiKey OAuth2 client-credentials v4 search, LCSC integration (described as regex HTML scraping), map_digikey/lcsc_value_to_form_column + KiCad value formatters, API keys and daily quota page, DigiKey and LCSC lookup page, Troubleshooting table and keyboard shortcuts (+19 more)

### Community 20 - "export_kicad_footprint.py"
Cohesion: 0.16
Nodes (24): angle_to_ki(), compute_arc(), _convert_solid_region(), drill_to_ki(), ExporterFootprintKicad, fp_to_ki(), _parse_solid_region_path(), rotate() (+16 more)

### Community 21 - "json"
Cohesion: 0.12
Nodes (10): get_manager(), reload_manager(), MailSettingsTab, ApiKeysTab, format_change_details(), load_keys(), mask_secret(), open_settings() (+2 more)

### Community 22 - "project_tree_import.py"
Cohesion: 0.18
Nodes (13): _error(), import_workbook(), _key(), _plan(), _positive(), preview_import(), read_workbook(), _records() (+5 more)

### Community 23 - "config.py"
Cohesion: 0.10
Nodes (9): find_library_root(), get_app_dir(), _path_setting(), resolve_api_keys_file(), resolve_db_backup_keep(), resolve_db_file(), resolve_symbol_library_dir(), _setting() (+1 more)

### Community 24 - "backup_database"
Cohesion: 0.13
Nodes (8): backup_database(), backup_file_name(), _copy_sqlite(), describe_backup_result(), list_backups(), prune_backups(), TestDailyBackup, DatabaseTab

### Community 25 - ".run"
Cohesion: 0.14
Nodes (12): done(), choose_revisions(), export_selected(), prepared(), loaded(), assign(), done(), ready() (+4 more)

### Community 26 - "ApiKeyManager"
Cohesion: 0.13
Nodes (4): ApiKeyManager, _utc_today(), TestApiSettings, LcscSettingsTab

### Community 27 - "lcsc_preview.py"
Cohesion: 0.17
Nodes (17): arc_points(), build_previews(), child(), children(), draw_graphic(), Drawing, footprint_preview(), transform() (+9 more)

### Community 29 - "api/lcsc_api.py"
Cohesion: 0.11
Nodes (5): describe_network_error(), _os_error_in(), _sub(), get_config(), get_logger()

### Community 30 - "Update Brensan Stock page"
Cohesion: 0.13
Nodes (23): Sync Database fills only missing BR numbers, Brensan KiCad Database Library (application), Brensan_Part_Number (BR code; '-' when not yet assigned), Check BOM List (added Excel columns, circuit breaker), <file>_DigiKey_Updated.xlsx output workbook, Export UD BR (collects components without BR code -> Brensan Part Numbers.xlsx list and Brensan Part Number Request.xlsx form), First Administrator Account window, Company stock server stokbul.php (http://192.168.1.184/stokbul.php) (+15 more)

### Community 31 - "symbol_lib.py"
Cohesion: 0.12
Nodes (10): choose_symbol_name(), list_symbols(), put_symbol(), _read(), _scan(), Symbol, SymbolLibraryError, _unescape() (+2 more)

### Community 32 - "Model3DConverter"
Cohesion: 0.11
Nodes (3): ca_bundle(), _discover_ca_bundle(), Model3DConverter

### Community 33 - "symbol_handlers.py"
Cohesion: 0.12
Nodes (12): _extract_pin_number(), h_A(), h_AR(), h_E(), h_P(), h_PG(), h_PL(), h_PT() (+4 more)

### Community 34 - "import_component"
Cohesion: 0.13
Nodes (8): import_component(), capture(), put(), _without_models(), LcscFixture, response(), TestLCSCClient, TestLCSCLibrary

### Community 35 - "LcscManagerWindow"
Cohesion: 0.15
Nodes (8): kicad_settings_folders(), TestLCSCWindow, LcscManagerWindow, success(), operation(), success(), worker(), success()

### Community 36 - "._run_bom_job"
Cohesion: 0.12
Nodes (8): worker(), start(), worker(), on_error(), on_success(), report(), run(), worker()

### Community 37 - "normalize_key"
Cohesion: 0.17
Nodes (15): apply(), apply_suggestion(), apply_selected(), get_default_reference_for_table(), get_entry_value_by_normalized_name(), get_value_for_table(), is_blank(), map_digikey_value_to_form_column() (+7 more)

### Community 38 - "ProductTreeWindow"
Cohesion: 0.15
Nodes (4): ProductTreeWindow, write(), save(), done()

### Community 39 - "_safe_float"
Cohesion: 0.19
Nodes (6): add_easyeda_text(), convert_to_mm(), EeSymbolText, _safe_bool(), _safe_float(), _safe_int()

### Community 40 - "reset_mail.py"
Cohesion: 0.16
Nodes (7): _dpapi(), load_delivery_settings(), load_settings(), save_settings(), send_reset_email(), validate_settings(), TestSMTP

### Community 41 - "lcsc_library.py"
Cohesion: 0.15
Nodes (8): valid_lcsc_id(), _atomic_write(), _bytes(), _commit(), get_usage_manager(), load_settings(), save_settings(), _validate()

### Community 42 - "Purchasing.py"
Cohesion: 0.11
Nodes (9): convert(), find_table(), gui(), run(), main(), normalize(), parse_date(), positive_integer() (+1 more)

### Community 43 - "Brensan Component Entry Skill"
Cohesion: 0.14
Nodes (18): Brensan_<Table>.kicad_sym symbol libraries, BrensanDB footprint nickname (Footprints.pretty), Common component columns (Reference, Value, Symbols, Footprints, ...), Component tables and field conventions, Example Resistor and Mosfet records, KiCad standard footprint naming table, Brensan Component Entry Skill, component_tool.py CLI (users/tables/check/lookup/suggest/add) (+10 more)

### Community 44 - "build_overview"
Cohesion: 0.19
Nodes (4): build_overview(), _revision_order(), _rows(), TestProjectOverview

### Community 45 - "HistoryTab"
Cohesion: 0.22
Nodes (4): filter_history(), read_log_history(), TestLogHistory, HistoryTab

### Community 46 - "BomEditWindow"
Cohesion: 0.17
Nodes (10): BomEditWindow, apply(), close(), assign(), show_suggestions(), selected(), source(), update() (+2 more)

### Community 47 - "component_tool.py"
Cohesion: 0.25
Nodes (12): cmd_add(), cmd_check(), cmd_suggest(), cmd_tables(), cmd_users(), company_footprint_name(), find_matches(), main() (+4 more)

### Community 48 - "password_reset.py"
Cohesion: 0.17
Nodes (5): EmailResetSession, _fingerprint(), issue_token(), revoke_token(), _token_hash()

### Community 49 - "folder"
Cohesion: 0.16
Nodes (6): export_part_number_request(), run_bom_job(), TestBomCircuitBreaker, TestBomDigikeyExport, TestRequestExport, folder()

### Community 50 - ".test_window_add_card_revision_from_card_and_revision_selection"
Cohesion: 0.13
Nodes (8): board_revisions(), compare_revisions(), pair_unique(), refs(), signature(), descendants(), RevisionCompareWindow, done()

### Community 51 - "footprint_suggestions.py"
Cohesion: 0.18
Nodes (7): _evidence_from_results(), google_settings(), _search(), _search_bing(), _search_duckduckgo(), _search_google(), SearchResults

### Community 53 - "save_project"
Cohesion: 0.19
Nodes (4): delete_project(), _project_audit_row(), save_project(), ProjectAuditTests

### Community 54 - "_attempt_search_with_credentials"
Cohesion: 0.16
Nodes (6): _attempt_search_with_credentials(), _count_request(), get_digikey_headers(), get_digikey_token(), invalidate_digikey_token(), TestTokenCache

### Community 55 - "Record form (auto-generated from the selected table's columns)"
Cohesion: 0.17
Nodes (16): Back up now button, BrensanDB.kicad_dbl (each component table registered as a KiCad library), BrensanDB.sqlite (shared copy Z:\Muhendislik\Kicad\BrensanDB.sqlite), Daily automatic database backup (_dbBackup, BrensanDB_YYYY-MM-DD.sqlite, last 10 kept), Datasheet label (opens link in default browser), Description / Package fields drive footprint suggestions (0402, 0603, 0805, 1206, SOT-23-5, SOIC-8, LQFP-48 7x7mm), Table -> KiCad footprint library mapping (Resistor->Resistor_SMD, Capacitor->Capacitor_SMD, Inductor->Inductor_SMD, Diode->Diode_SMD or LED_SMD, Fuse->Fuse; HandSolder variants ranked lower), Footprints label -> Footprint suggestions window (+8 more)

### Community 57 - "convert_fields_to_types"
Cohesion: 0.13
Nodes (13): add_easyeda_circle(), add_easyeda_ellipse(), add_easyeda_path(), add_easyeda_polygon(), add_easyeda_polyline(), add_easyeda_rectangle(), convert_fields_to_types(), EeSymbolCircle (+5 more)

### Community 59 - "complete_reset"
Cohesion: 0.28
Nodes (4): complete_reset(), AccountFixture, TestRecoveryTokens, consume()

### Community 61 - "lib_table.py"
Cohesion: 0.22
Nodes (7): _backup_once(), ensure_lib_entry(), _entry_line(), _escape(), LibTableError, read_lib_uri(), _unescape()

### Community 62 - "TestLocalSuggestions"
Cohesion: 0.24
Nodes (3): installed_footprints(), library_nickname(), TestLocalSuggestions

### Community 63 - "re"
Cohesion: 0.21
Nodes (8): get_lcsc_client(), get_lcsc_part_data(), parse_svg_path(), SvgPathClosePath, SvgPathEllipticalArc, SvgPathLineTo, SvgPathMoveTo, project_tree_label()

### Community 64 - "authenticate"
Cohesion: 0.25
Nodes (3): TestDesktopReset, TestLogin, authenticate()

### Community 65 - ".refresh_tree"
Cohesion: 0.16
Nodes (8): link_existing(), save(), done(), save(), save(), done(), remember(), text_width()

### Community 66 - "bom_edit_snapshot"
Cohesion: 0.19
Nodes (8): bom_edit_snapshot(), descendants(), open_sheet_dialog(), wait_job(), button(), descendants(), editor(), wait_job()

### Community 67 - "EeSymbol"
Cohesion: 0.26
Nodes (5): EasyedaSymbolImporter, _sanitize_component_name(), EeSymbol, EeSymbolBbox, EeSymbolInfo

### Community 68 - "rank_candidates"
Cohesion: 0.20
Nodes (7): _category(), _chip_matches(), has_package_information(), package_specs(), rank_candidates(), size_codes(), TestSizeCodes

### Community 69 - "suggest"
Cohesion: 0.27
Nodes (4): form_evidence(), suggest(), make_library(), TestSuggestFlow

### Community 71 - "Any"
Cohesion: 0.36
Nodes (3): Easyeda3dModelImporter, Ee3dModel, Ee3dModelBase

### Community 72 - ".loaded"
Cohesion: 0.22
Nodes (5): sheets_loaded(), load(), preview_ready(), imported(), load()

### Community 73 - "online_evidence"
Cohesion: 0.29
Nodes (3): online_evidence(), search_queries(), TestWebSearch

### Community 75 - "Desktop password reset (docs)"
Cohesion: 0.42
Nodes (8): Accounts & password recovery, Single-use 15-minute reset tokens + loopback page + TLS SMTP, Additive migration: Users.Email + Password_Reset_Tokens, digests only, Desktop password reset (docs), SMTP settings: %LOCALAPPDATA%\BrensanKiCadDB\SMTP-settings.json, DPAPI, BRENSAN_SMTP_PASSWORD, Token rules: 15 min, single use, 60 s cooldown, invalidated by account changes, User workflow: Forgot password? -> Send reset link -> browser Reset password -> 123456, Email password reset (Forgot password?)

### Community 77 - "main"
Cohesion: 0.22
Nodes (3): main(), report_fatal_error(), setup_logging()

### Community 78 - "hash_password"
Cohesion: 0.36
Nodes (3): TestPasswordHashing, hash_password(), verify_password()

### Community 82 - "extract_kicad_symbols_from_file"
Cohesion: 0.53
Nodes (5): get_kicad_symbol_file_for_component_type(), load_kicad_symbols_for_component_type(), extract_kicad_symbols_from_file(), format_kicad_symbol_name_for_library(), unescape_kicad_string()

## Ambiguous Edges - Review These
- `LCSC Manager (pinned core, no HTML scraping)` → `LCSC integration (described as regex HTML scraping)`  [AMBIGUOUS]
  CLAUDE.md · relation: semantically_similar_to

## Knowledge Gaps
- **3 isolated node(s):** `New project / New sub-project (two levels, folder, PROJECT_* audit)`, `Tests and run_smoke.py`, `Brensan KiCad component database app (Tkinter, SQLite, ODBC)`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 437 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **26 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `LcscManagerWindow` (2× useful, score=1.997459865)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ProductTreeWindow` connect `ProductTreeWindow` to `.refresh_tree`, `TestProductTree`, `bom_edit_snapshot`, `build_export`, `.loaded`, `Brensan_Kicad_DB.py`, `ProjectOverviewPanel`, `App`, `os`, `build_overview`, `BomEditWindow`, `product_tree_window.py`, `.test_window_add_card_revision_from_card_and_revision_selection`, `.select_revision`, `project_tree_import.py`, `.pick_component`, `.run`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `App` (e.g. with `LcscManagerWindow` and `ObsoleteProductsWindow`) actually correct?**
  _`App` has 9 INFERRED edges - model-reasoned connections that need verification._
- **What connects `New project / New sub-project (two levels, folder, PROJECT_* audit)`, `Tests and run_smoke.py`, `Brensan KiCad component database app (Tkinter, SQLite, ODBC)` to the rest of the system?**
  _3 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `db.py` be split into smaller, more focused modules?**
  _Cohesion score 0.052608311229000886 - nodes in this community are weakly interconnected._
- **Why does `App` connect `App` to `db.py`, `import_component`, `LcscManagerWindow`, `._run_bom_job`, `normalize_key`, `ProductTreeWindow`, `Brensan_Kicad_DB.py`, `lcsc_library.py`, `Purchasing.py`, `os`, `main`, `connection`, `.__init__`, `product_tree_window.py`, `TestSelectedStockDisplay`, `json`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `TestProductTree` (e.g. with `BomEditWindow` and `ObsoleteProductsWindow`) actually correct?**
  _`TestProductTree` has 4 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `LCSC Manager (pinned core, no HTML scraping)` and `LCSC integration (described as regex HTML scraping)`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._