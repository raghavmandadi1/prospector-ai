# Stable cell IDs, run records, and the cell cache

*Implemented 2026-08-01. Supersedes the AOI-relative grid described in
`docs/01_system_design.md`.*

This documents what was built for Workstreams A and C of "steps for raghav", and
— more usefully — the two places the implementation deliberately departs from
that spec, with the evidence for each.

---

## 1. The change

`scoring/grid.py` used to derive its projection from the AOI centroid and index
cells from the AOI's own bounding box (`cell_id = "c3_r7"`). `c3_r7` in one run
and `c3_r7` in the next were different patches of ground, so caching and
benchmarking were both impossible.

Cells are now indexed off a **fixed grid anchored to a single projection**:

```
cell_id = wa5070-<resolution>m-<col:06d>-<row:06d>
          e.g. wa5070-1000m-000349-000380
```

* `col`/`row` come from absolute projected coordinates, not the AOI bbox.
* The resolution ladder `[125, 250, 500, 1000, 2000, 4000, 8000]` doubles at
  every step and shares one origin, so each cell is exactly four of the step
  below — a quadtree. `parent_cell_id()` is exact containment.
* `cell_id_to_bbox()` recovers geometry from the id alone, which is why run
  records and benchmark reports can store bare ids.

Cells now also carry two geometries: `geometry` is the full unclipped square
(canonical — cached, and what the LLM is told about), `display_geometry` is that
square clipped to the AOI (map rendering only). Clipping the *analysis* cell
meant the LLM was reasoning about a sliver of terrain whenever a cell straddled
the polygon edge while the score was attributed to the whole cell.

---

## 2. Departure: EPSG:5070, not UTM 10N

**The spec called for hardcoding EPSG:32610 (UTM 10N) and failing on any AOI
outside roughly 126°W–120°W, on the stated grounds that "this project is scoped
to western WA (see CLAUDE.md → Scope)".**

That premise is wrong, and following it would have broken the most important
part of the tool.

`CLAUDE.md` → Scope says *"Washington State only"* and names Republic, Blewett,
Monte Cristo and Buckhorn. Counting district mentions in the two knowledge files
that exist:

| District | Mentions | Longitude | UTM zone |
|---|---|---|---|
| **Republic** | **33** | −118.74 | **11N** |
| Monte Cristo | 17 | −121.44 | 10N |
| **Metaline** | **17** | −117.06 | **11N** |
| Blewett | 15 | −120.67 | 11N |
| **Toroda** | **9** | −118.8 | **11N** |
| **Colville** | **9** | −117.9 | **11N** |

UTM zone 10N ends at 120°W. A hard fail east of 120°W would have rejected
Republic — the single most-cited district in the gold knowledge base — along with
Metaline, Toroda Creek, Colville, Okanogan and Orient. The same region is the
subject of `docs/04_usgs_of00_495_dataset.md`, whose source data is natively UTM
**11N**.

Per-zone IDs are not a fix either: an AOI straddling 120°W would produce two
incompatible cell families for the same ground, which defeats the entire point.

**EPSG:5070 (NAD83 / Conus Albers)** covers the whole state in one equal-area
projection. Verified in `backend/tests/test_grid.py`: a 1000 m cell measures
1 km² to within 2% at both Monte Cristo and Republic, and the two districts never
produce colliding ids.

The one thing lost is the spec's §12.1 idea of mentally converting a UTM cursor
readout into a cell id. The map now prints the **cell id under the cursor
directly**, which the spec itself calls the better option.

---

## 3. Departure: `USGSShadedReliefOnly` is not a usable default as specified

The spec recommends defaulting the basemap to `USGSShadedReliefOnly` whenever
results are displayed, states all five services were "verified live", and advises
deriving each one's `maxzoom` from its `maxScale` metadata.

Probing actual tiles at three points across the WA Cascades and NE Washington,
z3–z16 (2026-08-01):

| Service | Seeded zooms | `maxScale` implies |
|---|---|---|
| `USGSTopo` | 3–16 complete | 16 ✓ |
| `USGSImageryTopo` | 3–16 complete | 16 ✓ |
| `USGSImageryOnly` | 3–16 complete | 16 ✓ |
| `USGSShadedReliefOnly` | **3–8 and 12–13 only** | 16 ✗ |

All four advertise `maxScale: 9027.98`. Shaded relief has a hole at z9–11 and
nothing above z13, so the spec's own method returns the wrong answer for the
service it recommends as the default — the basemap goes blank across most of the
range where you draw and inspect an AOI. (Confirmed in the running app before the
fix: a blank map at the default statewide view.)

The `maxzoom: 16` advice for `USGSTopo` is correct and worth having; z17+ 404s.

**Fix:** shaded relief is drawn *over* `USGSTopo` with `minzoom: 12`. At z12–13 it
uses native tiles, above that MapLibre overzooms z13 (soft, but hillshade is
smooth), and below z12 the topo shows through. Never blank, and greyscale at
every zoom where a score ramp is actually being read.

---

## 4. What the caching rules buy

Only `score`, `confidence`, `evidence` and `data_sources_used` are cached.
`relative_score`, `percentile` and `tier` never are — they are properties of the
comparison, not the ground, and the same cell is `high` in a barren polygon and
`low` in a rich one.

The cache key hashes model, prompt version, **knowledge file contents** and
**spatial context**. The knowledge hash is the load-bearing one: the moment
`structure/gold.md` is written, every cached structure score becomes unreachable
and is recomputed. The spatial-context hash does the same when Known Gaps #2 is
fixed and the PostGIS query starts returning rows — cells that gain records
invalidate, cells that genuinely have none keep their entries.

Zero-confidence cells are never cached, so a parse failure cannot become
permanent.

---

## 5. Toponyms: what running it actually showed

The lexicon and matcher are in `backend/app/toponyms/` and
`backend/app/knowledge/toponyms/gold_wa.yaml`. Two findings from running the
first draft over all 22,712 WA GNIS names, both of which changed the design:

**Roughly half the strongest-tier hits were not mining.** Of 51 tier-1 "direct
workings" matches statewide, three systematic false-friend families accounted for
20: ship names (Discovery Bay, Port Discovery, Discovery Peak — HMS *Discovery*,
Vancouver 1792), railroad tunnels (Tunnel Creek ×4, Tunnel Island, Tunnel Flat),
and scenery (Prospect Point, Prospect Peak, Prospect Ridge). None of these
categories appear in the spec's §17 list of expected false friends, which
anticipated surnames, "Golden" as foliage, and sawmills. Those are now in the
anti-signal tier, suppressed by full name rather than by killing the word, so a
future "Tunnel Gulch" still scores. Tier 1 dropped 51 → 31 and the remainder is
almost entirely genuine.

**The signal is thinner than expected at the type locality.** Monte Cristo has
49 named GNIS features. Exactly two match the lexicon, and both are the district's
own name. The mining-era names there — Seventysix Gulch (the 76 claim), Poodle
Dog Pass, Morning Star Peak, Glacier Basin — carry no mineral vocabulary at all
and are undetectable by any regex over mineral terms.

That is worth stating plainly: at the best-documented gold district in western
Washington, the toponym feature finds only the label. This does not make it
worthless — tier-1 terms hit real ground elsewhere, and the draw-time overlay is
useful on its own — but it substantially deflates the expected score
contribution, and it sharpens §23's contamination warning from a theoretical risk
into the dominant effect at exactly the AOIs the benchmark uses as positives.

`benchmarks/labels.yaml` marks those AOIs `toponym_revealing: true` and the
benchmark reports **separation** and **toponym-blind separation** side by side.

---

## 6. Files

```
backend/app/scoring/grid.py           rewritten — fixed grid, ladder, two geometries
backend/app/runs/record.py            run records + provenance
backend/app/cache/cell_cache.py       SQLite cell cache
backend/app/toponyms/matcher.py       GNIS lexicon matcher
backend/app/api/reference.py          static overlay endpoints
scripts/build_gnis_extract.py         builds data/reference/gnis_wa.tsv
scripts/benchmark.py                  the harness
benchmarks/labels.yaml                ground truth — UNVERIFIED, see §7 below
frontend/src/components/Map/          basemaps, coords, LayerPanel, readout
```

---

## 7. Still owed

* **Ground truth.** `benchmarks/labels.yaml` has `verified: false` and every
  coordinate in it is an approximate district centre. The harness refuses to
  report working-percentile or recall@high until that flag flips.
* **Controls.** Zero of the 4–6 control AOIs are selected. A model that scores
  every batholith contact margin high passes on positives alone.
* **Occurrence data.** `data/reference/wa_occurrences.geojson` is not built —
  the USGS MRDS WFS returned 403 to tiled requests during this work. Until it
  exists, toponym corroboration (§21.1, the highest-value step in Workstream C)
  reports `corroboration: "unknown"` rather than corroborated/uncorroborated.
  The WA DNR `Gold_Silver_Locations` geodatabase already in `data/raw/` is the
  better source anyway.
* **The noise floor.** No repeat runs on a clean commit exist yet, so no delta
  can be called an improvement.
