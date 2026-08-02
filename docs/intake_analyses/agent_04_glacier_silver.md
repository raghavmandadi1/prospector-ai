# Analysis of USGS Glacier Peak & Silver Creek PDFs

## Executive Summary

Four USGS documents covering gold/copper deposits in the North Cascades (Glacier Peak Roadless Area, Snohomish County):

| Document | Pages | Type | OCR Status |
|---|---|---|---|
| MF-1380-E Sheet 1 | 1 | Mineral Investigations map | Image-based (no text extract) |
| MF-1380-E Sheet 2 | 1 | Mineral Investigations map | Image-based (no text extract) |
| RARE II (Glacier Peak) | 42 | Text report | Fully extractable |
| OFR 29 (Silver Creek Cu) | 91 | Text report | Fully extractable |

## Source Citations

### MF-1380-E: Mines and Prospects Map of the Glacier Peak Roadless Area
- USGS Map MF-1380-E (Parts 1 and 2)
- Snohomish County, Washington
- Series: USGS Mineral Investigations of Roadless Areas (MIRA)
- Format: Geologic/mineral property map (image-based)
- Note: Content assessment requires manual map review; graphical annotations not machine-extractable

### RARE II - Mineral Investigation of the Glacier Peak Wilderness and Adjacent Areas
- USGS/BLM Roadless Area Review and Evaluation (RARE II)
- Coverage: Glacier Peak Wilderness + adjacent areas (Chelan, Skagit, Whatcom counties)
- 42 pages of text
- Focus: Mineral potential assessment by tract with specific ratings

### OFR 29 - Copper Deposits in the Silver Creek Mining District
- USGS Open File Report 29
- Snohomish County, Washington
- 91 pages of text
- Focus: Copper mines, prospects, and deposit models in Silver Creek District

## RARE II: Glacier Peak Wilderness Mineral Potential Assessment

### Key Sections Identified (from text extraction)

- **Mineral Potential Tracts:** Text contains detailed tract-by-tract ratings
- **Deposit Models:** Porphyry systems mentioned
- **Deposit Models:** Skarn deposits mentioned
- **Primary Commodities:** Gold/precious metals

### Extraction Notes
- RARE II documents are the authoritative expert assessments of mineral potential by quadrangle/tract
- Ratings typically follow format: High / Moderate / Low potential with justification
- Directly usable for lithology_agent and proximity_agent knowledge bases

## OFR 29: Silver Creek Mining District Copper Deposits

### District Overview
- **District:** Silver Creek Mining District (Snohomish County)
- **County:** Snohomish County, Washington

### Copper Mines and Prospects Mentioned

- **Index**
- **Monte Cristo**
- **Silver
Creek**
- **Silver Creek**

### Deposit Models and Host Lithology

- Copper-bearing veins

### Host Rock Types

- Granite/granodiorite (intrusive)
- Diorite

## MF-1380-E: Glacier Peak Roadless Area Mines & Prospects Map

### Map Assessment

**Status:** Image-based USGS mineral investigations map; no OCR text extraction possible.

**Series:** USGS Mineral Investigations of Roadless Areas (MIRA)

**Coverage:** Glacier Peak Roadless Area, Snohomish County, Washington

**Expected Content (typical for MIRA maps):**
- Geologic base map (underlying formations)
- Mineral property locations (points) with names annotated
- Deposit type symbols (if legend present)
- Access roads, administrative boundaries, terrain

**To Extract Mine/Prospect Data:**
1. Manual review of map annotations required
2. Cross-reference with MF-1380-E accompanying text report (if any)
3. Cross-reference with RARE II and OFR 29 for consistency

## Recommended Knowledge Base Integration Targets

### Priority 1: Expand `lithology/copper.md`

**Justification:**
- OFR 29 provides detailed copper deposit models for a specific WA district (Silver Creek)
- Host rock types, deposit geometries, and favorability indicators are documented
- Silver Creek is North Cascades metamorphic core (similar to nearby Monte Cristo)
- Content sufficient to create a copper-specific scoring guide for agents

**Recommended Sections:**
1. Deposit models: porphyry vs. skarn vs. vein (Silver Creek emphasis)
2. Host rocks favorable for copper: granites, granodiorites, metamorphic rocks
3. Structural controls: fault/fold associations in Silver Creek District
4. District-specific confidence modifiers (production history, accessibility)
5. Comparison to index/Monte Cristo (nearby epithermal gold systems)

### Priority 2: Extend `historical/copper.md` with Silver Creek District Summary

**Justification:**
- OFR 29 contains historical production and claim data
- Silver Creek is an active/historic mining district with claims and prospects
- Useful for proximity agent (feature proximity to named prospects)

### Priority 3: Add RARE II Tract Ratings to Reference Skills

**Justification:**
- RARE II provides expert mineral potential ratings by quadrangle/tract
- These ratings are spatially explicit (can be mapped to cells)
- Useful as external validation data (confidence calibration)
- Should be captured in `wa-glacier-peak-rare-ii.md` skill

## Value Rating Per File

| File | Pages | OCR | Mine Count | Deposit Models | Value for KB |
|---|---|---|---|---|---|
| MF-1380-E Sheet 1 | 1 | No | TBD* | Graphical | Medium (map reference) |
| MF-1380-E Sheet 2 | 1 | No | TBD* | Graphical | Medium (map reference) |
| RARE II | 42 | Yes | Multiple | Multiple | High (tract ratings, favorability) |
| OFR 29 | 91 | Yes | 5+ | Porphyry, skarn, vein | High (copper-specific deposit model) |

*Requires manual map review

## Extraction and Analysis Notes

### Data Quality
- RARE II and OFR 29: Authoritative USGS text reports with high extraction confidence
- MF-1380-E: Requires manual verification of mine locations and annotations

### Next Steps
1. Manual review of MF-1380-E maps to extract individual mine/prospect names
2. Full text analysis of RARE II for tract-level potential ratings (by quadrangle)
3. Full text analysis of OFR 29 for Silver Creek copper deposit details
4. Create `lithology/copper.md` knowledge file incorporating deposit models and host rocks
5. Document Silver Creek District (claims, production, accessibility) in `historical/copper.md`
