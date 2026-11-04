---
type: "query"
date: "2026-11-02T12:01:50.756351+00:00"
question: "LCSC Manager ekranını tüm ekranı kaplayabilecek şekilde ayarlayabilmek istiyorum."
contributor: "graphify"
outcome: "useful"
source_nodes: ["LcscManagerWindow"]
---

# Q: LCSC Manager ekranını tüm ekranı kaplayabilecek şekilde ayarlayabilmek istiyorum.

## Answer

Expanded from graph vocabulary: [lcsc, manager, window, geometry, resize]. The graph locates LcscManagerWindow at ui/lcsc_manager_window.py:L16 and its initialization at L17. Source inspection found a transient window frame. Removed transient(parent) and explicitly enabled resizing to restore native Windows Maximize/Restore controls while retaining the modal grab.

## Outcome

- Signal: useful

## Source Nodes

- LcscManagerWindow