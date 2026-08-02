# Knowledge Base Intake — "FOR GITHUB" archive triage

**Date:** 2026-05-04
**Source archive:** `FOR GITHUB-20260505T053814Z-3-001.zip` (29 PDFs, 1 raster archive, 3 docx — 196 MB)
**Method:** 10 parallel agents, one analysis pass per file. Per-file analyses are in
`outputs/analyses/agent_NN_*.md` (claude session outputs, not in repo).

This document is the actionable synthesis: **what each file is, what it adds, where it goes
in the KB, and what to do next.**

---

## Stoplight summary

| # | File | Type | OCR | KB Value | Primary target |
|---|---|---|---|---|---|
| 1 | Gold in Wa Bulletin 42 (Huntting, ~1955) | Statewide gold reference | **Image-only — needs OCR** | HIGH (post-OCR) | `historical/gold.md`, `lithology/gold.md`, new `wa-bulletin42-gold.md` skill |
| 2a | Survey No. 7 — Index Mining District (Weaver 1912) | District bulletin | text ✓ | HIGH | `lithology/gold.md`, NEW `structure/gold.md`, NEW `wa-index-granodiorite-type-locality.md` skill |
| 2b | Bulletin 36 — Sultan Basin | District bulletin | image-only — needs OCR | MEDIUM (post-OCR) | `historical/gold.md` (Sultan placer) |
| 3a | Conconully IC 49 (Okanogan Co. Ag-Au) | District bulletin | text ✓ | HIGH | NEW Okanogan section in `lithology/gold.md` + `historical/gold.md`; consider `wa-conconully-okanogan-veins.md` skill |
| 3b | DGER Apex mine report (Money Creek, King Co.) | Single-mine report | text ✓ | **VERY HIGH** | `historical/gold.md` Money Creek entry; geochemistry/structure agent training |
| 3c | DGER Mystery & Justice (Monte Cristo, Snohomish Co.) | Single-mine report | text ✓ | **VERY HIGH** | `historical/gold.md` Monte Cristo augmentation (depth-zonation Cu-Pb-Zn → Au-Ag-As); geochemistry agent foundation |
| 4a | MF-1380-E Sheets 1+2 (Glacier Peak Roadless Area) | USGS map sheets | image-only | MEDIUM | proximity-agent training data once digitized |
| 4b | RARE II Glacier Peak Wilderness | Inventory report | text ✓ | HIGH | NEW `wa-glacier-peak-rare-ii.md` skill (tract favorability ratings) |
| 4c | OFR 29 Silver Creek copper district | District bulletin | text ✓ | HIGH | Foundation seed for **NEW `lithology/copper.md` + `historical/copper.md`** |
| 5 | Devils Canyon Mining (4 DMA/DMEA dockets) | Federal exploration loans | text ✓ (OCR'd packets) | LOW–MEDIUM (negative example) | `historical/gold.md` "DMEA-denied" pattern; calibration data point — disseminated Cu-Mo in pegmatitic granite, no economic Au |
| 6a | Bulletin 37 — Inventory of WA Mineral Resources (Huntting 1956) | Statewide all-commodity | text ✓ | **CRITICAL** | Seed material for silver/copper/lead/zinc/Mo/U knowledge files |
| 6b | Mining in Western WA (1909) | West-of-crest narrative | text ✓ | MEDIUM | `historical/gold.md` west-of-crest commentary; supports existing "LOW potential" rating |
| 7 | Bulletin 63 — Geology and Mineral Resources of King County (Livingston 1971) | County bulletin | text ✓ | **VERY HIGH** | NEW `wa-snoqualmie-batholith.md` skill; `lithology/gold.md` Snoqualmie margin section; `historical/gold.md` Money Creek/Miller River |
| 7b | "mineral resources King County.pdf" | duplicate (md5 match) | n/a | n/a | DELETE one copy |
| 8 | LK Hodges *Mining in the Pacific Northwest* (1897, 316 pp, 3 vols) | Historical compendium | **image-only — needs OCR** | HIGH (post-OCR) | `historical/gold.md` 1897-vintage descriptions; NEW `wa-historical-mining-1897-hodges.md` skill |
| 9a | Rockhound's Guide to WA Vol 1 + Vol 3 | Specimen-locality guides | image-only | LOW | Skip until OCR pipeline exists |
| 9b | "There's Gold in Them Thar Hills" (.45 Mines) | Single-property history | image-only | LOW–MED | Skip; one mine only |
| 9c | Caves of WA Information Circular 40 | Speleology | text ✓ | MEDIUM | Extract 8 Metaline Formation references → `lithology/gold.md` Metaline Skarn section |
| 9d | North Fork detailed hand-drawn map | User sketch | image-only | NOT KB material | Treat as user evidence layer, not knowledge |
| 10a | USGS OF01-501 methodology (Boleneus et al.) | Prospectivity model | text ✓ | **CRITICAL** | Quantitative W+/W-/contrast values → embed in `lithology/gold.md` + NEW `structure/gold.md` |
| 10b | newafull.tar.gz (OF00-495 raster archive) | ArcInfo .e00 grids | n/a — raster | **CRITICAL** | Move into repo `data/` as static asset; implement `connectors/usgs_of00_495.py` |
| 10c | Tertiary Formations of Western WA (Weaver 1916) | Foundation reference | text ✓ | already cited | Existing `wa-tertiary-stratigraphy` skill is correct; archive PDF, no new content needed |
| 10d | docx files (CURRENT links / similar models / sources to do) | Author notes | n/a | INFO | Already incorporated into TODOs below |

---

## What this changes in the KB

### Existing files that should be expanded immediately

**`backend/app/agents/knowledge/lithology/gold.md`** — add four new sections:

1. **Snoqualmie Batholith margin (King + Snohomish counties).** 17 Ma K-Ar age, ~250 sq mi
   outcrop. Mineralization is associated with the Tertiary Snoqualmie Granodiorite, NOT
   the older Mesozoic Mount Stuart Granodiorite — important discriminator. Money Creek /
   Miller River / Lennox Creek shear-zone corridor is mineralized; Permian limestone pods
   at the Grotto–Snoqualmie Pass contact are skarn-altered. (Source: Livingston 1971, Bul. 63.)
2. **Index District (Snohomish County).** Index Granodiorite type locality. Gunn Peak
   Formation (Carboniferous–Triassic metamorphics) hosts highest Au-Ag values; Index
   Granodiorite hosts larger but lower-grade Cu lenses. Arcuate vein system convex-N
   wrapping the batholith (N75°W eastern limb → E-W center → N45°-10°E western limb),
   70–90° dips. (Source: Weaver 1912, Survey No. 7.)
3. **Conconully District (Okanogan County).** Granodiorite–metamorphic contact setting:
   Similkameen Batholith intruding the Salmon Creek metamorphic sequence. Silver-dominant
   epithermal/mesothermal veins, gold subordinate. Distinct from the Republic-style low-S
   epithermal — vein style is closer to Slocan / mesothermal Ag-Pb-Zn. (Source: IC 49.)
4. **Quantitative WofE weights (NE WA, USGS OF01-501).** 50-site training set, 3 final
   evidence themes:
   - Lithology, 8 favorable units, 150 m buffer: W+=2.5, W-=-8.4, contrast 11.0
   - Normal faults, 1700 m buffer: W+=1.4, W-=-3.6, contrast 5.0
   - Placer gold, 4000 m: W+=2.3, W-=-1.4, contrast 3.7
   These are the actual numbers the lithology and structure agents should ground their
   scoring in. Sanpoil Volcanics (Evsf) hosts 20 of 50 training sites alone.

**`backend/app/agents/knowledge/historical/gold.md`** — add:

1. **Money Creek District (King County).** Apex Mine: gold-arsenopyrite vein, avg 1.2 oz Au/t
   + 7 oz Ag/t, SW¼SW¼ sec. 34, T26N R10E, 3800 ft elevation. 5 adits, aerial tramway,
   $300k production 1889–1941, closed by WPB Order L-208. 10 active lode claims (Cleopatra
   Mining, as of 2026). (Source: DGER Apex report.)
2. **Monte Cristo augmentation.** Mystery & Justice complex was 90% of district production
   (~310k tons, 1889–1915). Depth-zonation pattern: Cu-Pb-Zn shallow (<250 ft) →
   Au-Ag-As deep. Boom-bust closure drivers were arsenic at depth + E&MCR railroad
   washout costs, not ore depletion. Now in Henry M. Jackson Wilderness. Acid mine drainage
   carries As. (Source: DGER Mystery & Justice report.)
3. **Conconully District (Okanogan).** ~$350k production 1889–1964 (incomplete). 25+
   indexed mines. Principal producers: Arlington, First Thought, Last Chance, Fourth of July.
4. **Index District (Snohomish).** 20+ documented mines. Sunset (36 claims, 10 carloads
   5% Cu shipped), Lost Creek (highest Au/Ag — 0.2 oz Au/t, 2.5–5 oz Ag/t), Non-Pareil,
   Ethel Consolidated, Copper Bell. Closed post-1902 Cu price collapse + transport costs.
5. **Devils Canyon (Buena Vista district, King County) — DMEA-denied negative case.**
   Sections 26–27, T25N R10E, 8 claims. Disseminated Cu-Mo in K-feldspar granite + pegmatite,
   no economic Au, sub-economic Cu (0.35–0.55%) and Mo (~0.01–0.30%). Both DMA 1658 and
   DMEA 3557 denied. **Use as a calibration negative**: "named historic mine + denied
   federal cost-share + no production" should be a *modest negative* for the historical
   agent, not a positive.

### New files to create

| Path | Purpose | Source |
|---|---|---|
| `backend/app/agents/knowledge/structure/gold.md` | Quantitative structural scoring rubric | OF01-501 fault weights; Index District arcuate vein geometry; Bacon Creek Fault (Republic); Money Creek shear corridor |
| `backend/app/agents/knowledge/lithology/copper.md` | Copper lithology baseline | OFR 29 Silver Creek; Bulletin 37 (Holden multi-metal in Chelan; Skagit Copper Belt); Index District Cu lenses |
| `backend/app/agents/knowledge/historical/copper.md` | Copper historical patterns | OFR 29; Bulletin 37; Mining in Western WA narrative (metallurgical-mismatch failure mode of west-of-crest base metals) |
| `backend/app/agents/knowledge/lithology/silver.md` | Silver baseline | Bulletin 37 (46+ Chelan Co. properties; Conconully; Stevens/Pend Oreille) |
| `backend/app/agents/knowledge/historical/silver.md` | Silver historical | Bulletin 37; Conconully IC 49 |
| `.claude/skills/wa-snoqualmie-batholith.md` | Snoqualmie Batholith reference (user priority) | Bulletin 63 (Livingston 1971) |
| `.claude/skills/wa-index-granodiorite-type-locality.md` | Index Granodiorite type locality | Survey No. 7 (Weaver 1912) |
| `.claude/skills/wa-glacier-peak-rare-ii.md` | RARE II tract ratings for Glacier Peak Wilderness | RARE II report |
| `.claude/skills/wa-bulletin42-gold.md` | Canonical citation for Bulletin 42 (Huntting) | Bulletin 42 (post-OCR) |
| `.claude/skills/wa-historical-mining-1897-hodges.md` | Canonical citation for Hodges 1897 | LK Hodges (post-OCR) |

### Static reference dataset to commit

`newafull.tar.gz` (OF00-495) is **production-ready** for the loader described in
`docs/04_usgs_of00_495_dataset.md`:
- ArcInfo .e00 export of 4 rasters: `newageol` (lithology, 50 m), `newafold` (folds, 50 m),
  `newafaul` (faults, 100 m), `newadike` (dikes, 200 m)
- Native CRS: UTM 11N / NAD27 (EPSG:26711)
- BBox: 48–49°N, 117–120°W (6 quads: Colville, Chewelah, Republic, Nespelem, Omak, Oroville)
- GDAL-readable, reprojection path to EPSG:4326 is straightforward

**Action:** move the tarball into `data/static/usgs_of00_495/` in the repo and unblock
implementation of `backend/app/connectors/usgs_of00_495.py` per the plan in
`docs/04_usgs_of00_495_dataset.md`.

---

## Files needing OCR before they can be used

These are 200-dpi CCITT image scans with no embedded text. Until OCR'd, they cannot be
mined for KB content:

1. **Gold in WA Bulletin 42** — 162 pp. The single most authoritative WA gold reference. **Highest OCR priority.**
2. **LK Hodges, *Mining in the Pacific Northwest* 1897** — 3 vols / 316 pp.
3. **Bulletin 36 — Sultan Basin** — ~18 pp.
4. **MF-1380-E Sheets 1+2 (Glacier Peak Roadless)** — 2 map sheets. (OCR will not help — these are graphical maps; need digitization, not OCR.)
5. **Rockhound's Guide Vol 1 + Vol 3** — low-priority specimen guides.
6. **".45 Mines" history** — single property; defer.
7. **North Fork hand-drawn map** — not KB material; user evidence layer at most.

A simple `tesseract`-based batch on items 1–3 would unlock most of the remaining value.
Estimated effort: 30–60 min compute on the 478 pages of items 1+2+3.

---

## Duplicates and housekeeping

- `Geology and Mineral Resources of King County.pdf` and `Reports/mineral resources King County.pdf` are **byte-identical** (md5 `cdecb8a16ec78738293531766b5b3003`). Drop one copy.
- Three docx files (`CURRENT links`, `similar models`, `sources to do`) are author notes.
  Their content has been folded into this report and into existing TODOs:
  - `CURRENT links` → flagged Snoqualmie Batholith priority (handled in §"existing files")
  - `similar models` → 4 GitHub repos for ML mineral-prospectivity reference (note for
    `docs/03_implementation_plan.md` if/when an ML scoring module is added)
  - `sources to do` → checklist of public APIs that are already reflected in
    `app/connectors/*` and the `wa-*` skills

---

## Recommended order of work

1. **Commit `newafull.tar.gz`** to `data/static/usgs_of00_495/` and implement the loader
   (highest data leverage; everything depends on it).
2. **Write `structure/gold.md`** using the OF01-501 fault weights + Index arcuate-vein
   geometry. Currently the structure agent has no knowledge file at all.
3. **Expand `lithology/gold.md`** with the 4 new sections above (Snoqualmie Batholith,
   Index, Conconully, OF01-501 quantitative weights).
4. **Expand `historical/gold.md`** with Money Creek (Apex), Monte Cristo augmentation
   (Mystery & Justice), Conconully, Index, and the Devils Canyon negative-example.
5. **Create the new skill files**: `wa-snoqualmie-batholith.md` first (user priority),
   then `wa-index-granodiorite-type-locality.md`, then `wa-glacier-peak-rare-ii.md`.
6. **Seed `lithology/copper.md` + `historical/copper.md` + `lithology/silver.md` +
   `historical/silver.md`** from Bulletin 37 + OFR 29.
7. **Run OCR on Bulletin 42 + LK Hodges + Sultan Bulletin 36**, then re-extract.

Steps 2–6 can each be done independently and in any order. Step 1 unblocks the connector
work but is parallel to KB writing. Step 7 unblocks the highest-value remaining content
but requires external OCR tooling.

---

*Per-file analyses (full detail) are in `outputs/analyses/agent_NN_*.md` in the Cowork
session output folder. They contain the verbatim extracts and citation pages this synthesis
draws from.*
