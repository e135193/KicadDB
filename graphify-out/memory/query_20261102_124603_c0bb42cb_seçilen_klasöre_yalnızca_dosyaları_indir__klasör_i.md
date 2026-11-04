---
type: "query"
date: "2026-11-02T12:46:03.895236+00:00"
question: "seçilen klasöre yalnızca dosyaları indir. klasör içerisinde klasöre dosya indirme. tüm indirilenler tek bir klasör de olsun. 3d görüntüleme arayüzü yok. nasıl 3d görüntüleyebilirim"
contributor: "graphify"
outcome: "useful"
source_nodes: ["LcscManagerWindow", "lcsc_library.py"]
---

# Q: seçilen klasöre yalnızca dosyaları indir. klasör içerisinde klasöre dosya indirme. tüm indirilenler tek bir klasör de olsun. 3d görüntüleme arayüzü yok. nasıl 3d görüntüleyebilirim

## Answer

Expanded vocabulary: [lcsc, library, model, preview, download]. Verified lcsc_library.py and LcscManagerWindow source. Download CAD files now passes flat=True so symbol, footprint, STEP, WRL and replacement backups are files directly in the chosen folder; staging is temporary elsewhere. Added step_preview.py with real STEP tessellation through cadquery-ocp 7.9.3.1.1 and ui/step_preview.py as a 3D Model tab with rotate, pan, zoom, Top/Front/Iso/Fit and local Open STEP. CAD loading/rendering run in workers with stale-result protection. Tests validate real STEP geometry, flat downloads and asynchronous selection changes.

## Outcome

- Signal: useful

## Source Nodes

- LcscManagerWindow
- lcsc_library.py