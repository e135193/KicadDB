---
type: "query"
date: "2026-11-02T13:39:38.947607+00:00"
question: "mevcutta seçmiş olduğum malzeme için footprint önizlemesi görüntülenmiyor bunu düzelt"
contributor: "graphify"
outcome: "useful"
source_nodes: ["suggest()", "package_specs()", "Preview"]
---

# Q: mevcutta seçmiş olduğum malzeme için footprint önizlemesi görüntülenmiyor bunu düzelt

## Answer

Expanded vocabulary: footprint preview package suggest step color render. Graph located suggest() and package_specs() in footprint_suggestions.py, and Preview in lcsc_preview.py. Current source inspection found missing TDSON/TSDSON support and no saved-footprint input. Added exact saved-ID priority, a missing-assignment message and distinct TDSON/TSDSON matching. Read-only inspection found saved BrensanDB:PG-TDSON-8-FL versus installed PG-TDSON-8-34. Local candidate now previews. STEP reader now preserves XCAF colours and uses pixel depth. All 348 tests passed.

## Outcome

- Signal: useful

## Source Nodes

- suggest()
- package_specs()
- Preview