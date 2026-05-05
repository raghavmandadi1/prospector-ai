---
name: wa-historical-geology-source
description: Guide for citing, interpreting, and reconciling Weaver's 1916 western Washington Tertiary nomenclature with modern stratigraphic usage. This skill should be used by the historical_agent (and any other agent producing evidence strings) whenever Weaver (1916) is cited, when the bulletin's plates/maps are referenced, or when the analysis needs to translate 1916 area-based unit names into modern formation names (Crescent, Puget Group, Lincoln Creek, Blakeley, Astoria, etc.). Also documents which chapters of the source PDF were available vs. missing, so warnings can be raised appropriately.
source: Weaver, C.E. (1916). The Tertiary Formations of Western Washington. Washington Geological Survey Bulletin No. 13.
---

# Citing & Interpreting Weaver (1916)

The Weaver bulletin is a foundational early-20th-century synthesis of Pacific Northwest Tertiary geology. Many of its observations remain the only published descriptions of specific outcrops, but its nomenclature predates modern formal stratigraphy. This skill provides the conventions for using it correctly in agent outputs.

## Canonical Citation

> Weaver, C.E. (1916). *The Tertiary Formations of Western Washington.* Washington Geological Survey, Bulletin No. 13. Olympia: Frank M. Lamborn, Public Printer. 327 pp., 30 plates.

Use this exact form whenever cited in agent `evidence` or `data_sources_used`. Internal short form: `Weaver_1916_WGS_Bulletin_13`.

## Plates / Maps Inventory

Several large-scale geologic maps are bound "in pocket" in the original bulletin and may be referenced by plate number in agent reasoning:

- **Plate II** — Preliminary geologic map of western Washington (overview).
- **Plate III** — Areal and structural map of southwestern Washington.
- **Plate IV (Maps A–D)** — Areal/structural maps for: (A) western/northern Olympic margin, (B) Puget Sound–Cascade traverse belt, (C) Quimper Peninsula, (D) Cathcart–Cherry Valley region.
- **Plate XVII** — Cape Flattery geologic map.
- **Plate XXX** — Structural geologic map of western Washington.

When citing a plate, use the form `Weaver (1916) Plate IV-B` so it is unambiguous.

## Nomenclature Reconciliation (Weaver → Modern)

Weaver organized Eocene units by *geographic area* rather than by formal formation names. Modern stratigraphic practice uses lithostratigraphic formation names. Approximate correspondences:

| Weaver (1916) | Modern usage |
|---|---|
| Eocene basalts of southwest WA / Olympic margin | **Crescent Formation** |
| Eocene sedimentary rocks of King–Pierce coal fields (Newcastle, Renton, Green River, Cedar Mtn) | **Puget Group** (Renton Formation, Tukwila Formation, Tiger Mountain Formation) |
| Bayne / Franklin / Kummer "members" of Green River | Subsumed into Puget Group; Franklin sandstone still used informally |
| Eocene of Olequa–Winlock (Lower Cowlitz Valley) | **Cowlitz Formation** / McIntosh Formation |
| Oligocene marine of Puget Sound and SW WA | **Lincoln Creek Formation** (Blakeley Formation in some areas) |
| Oligocene faunal zones (*M. lincolnensis*, *T. porterensis*, *A. gettysburgensis*) | Still used as biostratigraphic markers |
| Lower Miocene of Strait of Juan de Fuca | **Clallam Formation** |
| Upper Miocene of Grays Harbor / Quinault | **Astoria Formation** / Montesano Formation |
| Hoh formation | **Hoh rock assemblage** (now recognized as a tectonic mélange of accreted Eocene–Miocene material) |
| Index granodiorite | **Index batholith** / Mount Index pluton |
| Snoqualmie granodiorite | **Snoqualmie batholith** (Miocene) |
| Enumclaw volcanic series | **Fifes Peak Formation** / Stevens Ridge Formation in part |

Always present both names when bridging to modern data: `"Eocene Puget Group sediments (Weaver 1916: Newcastle Hills area)"`.

## Source-PDF Coverage Caveat

The uploaded PDF contains pages 1–169 of the bulletin (front matter through the start of Chapter IV / Oligocene). The following chapters are *not* in the uploaded PDF and were not consulted when building the companion skills:

- Chapter IV (Oligocene Formations) — only the introduction and faunal-zone definitions are present.
- Chapter V (Miocene Formations).
- Chapter VI (Pleistocene Formations).
- Chapter VII (likely structure / regional summary — TOC unclear).
- **Chapter VIII (Petroleum Deposits)** — the entire SW WA + Olympic Peninsula petroleum chapter, including the Quinault, Queets, Hoh, Quillayute, and Ozette Lake districts and the drilling-operations record.
- Appendix.

When agents rely on Weaver (1916) for analyses involving Miocene units, Pleistocene cover, or **petroleum**, they MUST add a warning:

```
warnings: ["Weaver (1916) Chapters IV-VIII not present in source PDF; supplement with modern USGS / WA DNR Geology Division data before relying on Miocene, Pleistocene, or petroleum interpretations."]
```

## How to Use in Agent Reasoning

1. When the historical_agent surfaces an old citation, route through the nomenclature table above to give the user both the historical and modern names.
2. When an evidence string references a Weaver area (e.g., "Newcastle Hills"), append the modern equivalent in parentheses for downstream analyst clarity.
3. Always include `data_sources_used: ["Weaver_1916_WGS_Bulletin_13"]` plus the chapter number (e.g., `..._Ch_III`) when the cited content is from a chapter that *is* in the PDF.
4. Raise the missing-chapters warning whenever the analysis touches Miocene, Pleistocene, or petroleum content.
