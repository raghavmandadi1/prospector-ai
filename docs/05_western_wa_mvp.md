# GeoProspector — Western WA Gold MVP

> **What this document is.** A focused MVP plan for adapting the USGS weights-of-evidence (WoE) methodology from OF 01-501 to **Snohomish & Kittitas counties** (and the broader Cascade metallogenic belt). It complements — and intentionally narrows — the broader plan in `03_implementation_plan.md`.
>
> **Reading order.** §1 sets scope, §2 picks the deposit models, §3 names the predictor themes, §4 lists the data sources, §5 explains how the LLM augments the WoE core, §6 is the 6-week action plan, §7 is success criteria.

---

## 1. Scope clarification

The user-facing pitch is "western Washington — Snohomish, Kittitas". The *productive* ground in those counties does **not** sit in the Puget Lowland — it sits in the Cascade metamorphic core and Cascade transition zone. The MVP AOI envelope is:

| | Bounding | Why |
|---|---|---|
| **N–S** | ~47.0°–48.5° N | Captures Wenatchee–Blewett–Swauk through Index–Monte Cristo |
| **E–W** | ~120.0°–122.0° W | Cascade crest ± ~80 km |
| **Counties** | Snohomish, King (E), Kittitas, Chelan (W edge) | The four touching the metallogenic belt |

Counties to **exclude from MVP** even though they're administratively "western WA": Pierce (S), Skagit (N), Whatcom (further N — Slate Creek is interesting but adds complexity), and the entire Puget Lowland west of ~122°W (negligible primary gold per the existing `lithology/gold.md`).

---

## 2. Deposit models to encode

OF 01-501's deposit model — USGS **25a/25c** epithermal hot-spring Au–Ag — does **not** apply here. The Cascade core never saw the Eocene caldera/graben / lacustrine setting that controls Republic-style deposits. Three deposit models cover ~all of the historical production in the MVP envelope:

| USGS Model | Style | MVP districts | Rough WA production |
|---|---|---|---|
| **36a** Low-sulfide Au-quartz vein | Orogenic / mesothermal | Monte Cristo, Index, Sultan, Wenatchee, Blewett (lode) | ~0.6–1.0 Moz combined |
| **22c** Polymetallic vein | Polymetallic Au-Ag-Cu-Pb-Zn in shears, often pluton-related | Monte Cristo (overlaps 36a), Silver Creek, parts of Slate Creek | included above |
| **39a** Placer Au | Stream-reworked detrital gold | Swauk, Liberty, Peshastin, Skykomish | ~0.1–0.4 Moz |

Recommendation: **start with one composite "Cascade vein gold" descriptive model that fuses 36a + 22c**, treat 39a (placer) as a *separate output layer* rather than blending it in. Why: orogenic and polymetallic veins share the same controlling geology in the Cascades (intrusive contacts, schist/amphibolite contrasts, NE/NW shear sets); placer gold has fundamentally different controls (drainage, gradient, gravel age) and deserves its own scoring path.

For the MVP, **drop the existing epithermal_agent code path** when AOI ∩ Cascade envelope is non-empty. The lithology agent's current gold KB already distinguishes the three styles correctly; what we need to do is route around model 25a inside this AOI.

---

## 3. Predictor themes — the WA-equivalent of OF 01-501 Table 7

This is the heart of the methodological port. OF 01-501 ended up with three accepted predictors (lithology / NW-NNE normal faults / placer sites). Below is the analogous candidate set for Cascade vein gold, with first-pass buffer distances drawn from published district mapping (refine empirically against a WA training set in §6).

| Predictor theme | Source | First-pass buffer | Replaces what in OF 01-501 |
|---|---|---|---|
| **Tertiary intrusive contacts** (Snoqualmie, Index, Mt. Stuart, Cloudy Pass batholiths; Cretaceous granodiorites where present) | WA DGER 1:100k geology + USGS SGMC | 500 m–2 km (test peak contrast) | Klondike Mtn / Sanpoil host rocks |
| **Metamorphic host rocks** (Chiwaukum Schist, Index Schist, Tonga Fm, Swakane Gneiss; especially schist↔amphibolite contacts) | WA DGER lithology layer | 250 m | n/a — new, not present in NE WA model |
| **Major structural corridors** (Straight Creek FZ, RMFZ, Entiat FZ, NE-trending shear sets) | WA DGER active faults + USGS QFFDB + LiDAR-derived lineaments | 1–2 km | NW-NNE normal faults (1700 m) |
| **Known mines / prospects** (MRDS + WA DGER IWM + MinDat) | mrdata.usgs.gov, gis.dnr.wa.gov | 500 m–1.5 km | Training set + proximity theme |
| **Placer sites** (Swauk, Peshastin, Skykomish, Sultan) | WA DGER IWM placer layer | 4 km (same as OF 01-501) | Same role |
| **Stream-sediment Au + As anomalies** (As is the stronger pathfinder for orogenic Au) | USGS NGDB / NURE | 500 m IDW | Same role; OF 01-501 *rejected* this theme but Cascade orogenic systems have stronger As signature |
| **Aeromagnetic high–low transitions** (intrusive margins) | USGS state aeromag compilation | 1 km | n/a — new |
| **LiDAR-detected mining anthropogenics** (adit benches, dump piles, prospect pits) | WA DNR LiDAR Portal (≤1 m DEM) | 250 m | n/a — uniquely possible in WA because of statewide LiDAR |

The LiDAR layer is the biggest "free upgrade" available to us that OF 01-501 didn't have. Statewide ≤1 m LiDAR makes hundred-year-old shafts and dumps directly visible; we can derive a "historical disturbance density" predictor that's effectively a free training-set densification.

**Wilderness/exclusion mask** (PAD-US + tribal lands) is applied *after* scoring, as a UI overlay, not as a predictor — we still want to score those areas for completeness even if they're un-prospectable.

---

## 4. Data sources to wire first

Verified live during research (May 2026). Marked **★** = MVP-critical, ☆ = nice-to-have.

| Source | Endpoint | Theme | Notes |
|---|---|---|---|
| **★ USGS MRDS** | `mrdata.usgs.gov/services/mrds` (WMS/WFS) | Mines, training set | Already in plan |
| **★ WA DGER Mines & Minerals** | `gis.dnr.wa.gov/site1/rest/services/Public_Geology/Mines_and_Minerals/MapServer` | WA-authoritative mines/prospects + historical districts | ArcGIS REST — query by bbox, paginate |
| **★ WA DGER 1:100k Geology** | data-wadnr.opendata.arcgis.com | Lithology, structure, faults | Bedrock, surficial, structure layers |
| **★ Macrostrat** | `macrostrat.org/api/v2/geologic_units/map` | Lithology + age framework | Already in plan |
| **★ USGS NGDB** | `mrdata.usgs.gov/ngdb` | Geochem | Filter to WA + relevant analytes (Au, As, Sb, Hg, Pb, Zn) |
| **★ WA DNR LiDAR Portal** | `lidarportal.dnr.wa.gov` (WMS + tile downloads) | Topography, anthropogenic features | Largest unique advantage |
| **★ BLM MLRS** | `mlrs.blm.gov` | Active claims | Existing plan |
| **★ USGS QFFDB** | `earthquake.usgs.gov/earthquakes/qfaults` | Faults | Existing plan |
| **★ USGS SGMC** | ScienceBase | Backup statewide geology | Cross-check WA DGER |
| ☆ USGS aeromag compilation | ScienceBase / WMS | Geophysics | Phase 2 |
| ☆ Sentinel-2 / Landsat 8/9 | STAC (Planetary Computer / EarthSearch) | Alteration spectral indices | Phase 2; clouds are a real problem in W. WA |
| ☆ USGS PP1802 | ScienceBase | Curated deposits | Cross-validation |
| ☆ MinDat | `mindat.org/api` | Mineralogy | Existing plan |
| ☆ USFS Wilderness / PAD-US | `usgs.gov/programs/VHP/pad-us` | Exclusion mask | Display layer |
| ☆ USGS Bull. 1359 (N. Cascades) / WA DGER GM-22 | PDF | Historical / training-set seed | Manual digitization — load into knowledge base |

We can defer USGS EarthMRI Cascades — release is staggered through 2026 and the MVP shouldn't block on it.

---

## 5. How LLM power augments the WoE core

OF 01-501 was rule-based. Our value-add is keeping the numerical rigor (W+/W−, contrast, posterior probability) and inserting LLMs at exactly the steps where rule-based systems struggle. The split:

| Layer | Implementation | Why this layer |
|---|---|---|
| **Predictor weight calibration** | Numerical (W+, W−, contrast over a training set; same as OF 01-501) | LLMs can't compute likelihood ratios; numerical is auditable |
| **Per-cell scoring** | Numerical posterior probability from generalized predictor maps | Reproducible, fast, defensible |
| **Lithology/structure interpretation** | LLM (already implemented) — given the unit name, age, modal mineralogy, and adjacent units, judge favorability for orogenic Au | Geologic units have rich text descriptions; LLM extracts what numerical rules can't |
| **Geochemical anomaly narrative** | LLM — given multi-element values + spatial context, name the likely deposit-style signature | Multi-element interpretation is the classic "judgment" task |
| **Historical evidence reasoning** | LLM (already implemented) — read MRDS comments, BLM patent descriptions, Bulletin 1359 OCR | Unstructured text mining |
| **Per-cell evidence drilldown** | LLM — convert the predictor stack into 3-bullet plain-English rationale | UX requirement, not WoE-required |
| **Training-set adjudication** | LLM-assisted human review — "is this MRDS record really an orogenic Au mine, or is it placer / skarn / prospect-only?" | OF 01-501 spent 100 person-days on data prep; LLM can cut that |

Concretely: the orchestrator already returns per-cell `score`, `confidence`, `evidence`. Add a *parallel* numerical pipeline that computes WoE posterior probability for each cell using the predictor maps from §3, store it as `wofe_posterior` on `ScoredCell`, and have the scoring engine present BOTH (LLM-weighted composite + WoE posterior) in the evidence drawer. They should agree most of the time; disagreement is the interesting case worth surfacing.

---

## 6. The 6-week action plan

Builds on the existing 35-day plan in `03_implementation_plan.md`. Keep its phase numbering; add deltas and one new phase (Phase 4.5 — WoE numerical core).

### Week 1 — scope the MVP envelope and seed the training set
1. **Hard-code MVP AOI bounding box** (47.0°–48.5° N, 122.0°–120.0° W) as a feature flag / config so all connector ingest jobs are spatially constrained for now. Saves ~95% of API calls and DB rows. *Owner: backend.*
2. **Write WA gold training-set spec.** Define: which MRDS + WA DGER IWM records qualify (commodity ∈ {Au, Au-Ag}, deposit-type tag matches 36a or 22c, has production OR ≥3 vein descriptions, geocoding ≤500 m). Output: a Pydantic model + CSV under `backend/app/scoring/training_sets/wa_cascade_au.csv`.
3. **Manually curate ~30–50 training sites** for the MVP envelope (Monte Cristo, Index, Sultan, Silver Creek, Wenatchee, Blewett lode, Swauk lode subset). Use Bulletin 1359 + GM-22 as authority. Two passes: LLM extracts candidates from MRDS dump → human accepts/rejects.
4. **Add a `deposit_model_code` column** to `Feature` so we can filter by 36a/22c/39a downstream.

### Week 2 — wire the WA-specific connectors
5. Implement **WA DGER Mines & Minerals connector** (ArcGIS REST query, paginated). New file `backend/app/connectors/wa_dger_mines.py`. Use `/new-connector` slash command.
6. Implement **WA DGER 1:100k geology connector** — bedrock, structure, surficial layers. Three feature types or one polymorphic unit. Same path.
7. Implement **WA DNR LiDAR connector** — *not* a feature ingester; instead a raster fetcher that pulls the LiDAR tiles intersecting AOI, derives slope + a "high-frequency relief residual" raster, and persists as a COG to MinIO. New module `backend/app/pipeline/lidar.py`.
8. Tighten the existing **MRDS connector** to filter by AOI envelope and commodity = Au / Ag-Au / polymetallic.
9. **Smoke-test ingestion** by syncing the AOI envelope; expected counts: a few thousand WA DGER mineral occurrences, ~15k MRDS features statewide → ~500–1500 in the AOI.

### Week 3 — grid + WoE numerical core (Phase 4.5)
10. **Generate the analysis grid** at 250 m resolution over the MVP envelope and persist (~1.4 M cells; precompute, don't regenerate per job).
11. **Build the predictor maps** in `backend/app/scoring/wofe.py`:
    - `build_predictor_map(theme: str, buffer_m: int) -> RasterizedTheme` — takes a feature theme + buffer band, returns binary "inside / outside the pattern" raster aligned with the analysis grid.
    - `compute_weights(theme: BinaryRaster, training_sites: List[Point]) -> Tuple[w_plus, w_minus, contrast, std_contrast]` — pure NumPy / SciPy, no LLM.
    - `combine_predictors(themes: List[BinaryRaster], weights: List[float]) -> PosteriorMap` — additive in log-odds space; convert to posterior.
12. **Calibrate buffer distances** by sweeping each theme over [50, 100, 250, 500, 1000, 2000, 4000] m and picking the one that maximizes studentized contrast against the training set — same procedure as OF 01-501 §"cumulative weights".
13. **Persist calibrated weights** as a versioned JSON in `backend/app/scoring/wofe_models/wa_cascade_au_v1.json` so they're reproducible and auditable.

### Week 4 — orchestrator integration + evidence drilldown
14. **Add a 7th agent: `WofeAgent`** — reads precomputed posterior raster, returns `ScoredCell` with `score = posterior`, `confidence` from the training-set support count for that cell's predictor combination.
15. **Update the orchestrator** to run the WofeAgent in parallel with the six existing LLM agents.
16. **Update the scoring engine** to surface both the LLM-weighted composite *and* the WoE posterior side-by-side, with a flag `agreement: "concordant" | "discordant"` based on whether they're in the same tier.
17. **Update the EvidenceDrawer UI** to show the WoE posterior, the contributing predictor themes, and the "discordant" flag where applicable.

### Week 5 — output, exports, and end-to-end testing
18. Run **end-to-end analysis** on three preselected test AOIs: (a) ~50 km² around Monte Cristo (should score very high — has 16+ training sites), (b) ~50 km² around Swauk (should score high for placer, moderate for vein), (c) ~50 km² in the Puget Lowland west of the envelope (should score negligible — control case).
19. **Manually validate** the high-scoring cells against published mine locations. Aim for ≥80% recall of training sites in "favorable" tier.
20. Add CSV / GeoJSON exports (already specified in the parent plan).

### Week 6 — calibration, polish, soft launch
21. **Backtest weight presets** — leave-one-out cross-validation on the training set; report mean rank of held-out site.
22. **Document mistakes** in `.claude/mistakes-log.md` as you go (per project convention).
23. **Write a one-page "Cascade gold MVP results" report** — three test AOIs, per-tier counts, top-10 predicted unmined cells with rationale.
24. Ship to a small number of friendly users (3–5 prospectors) for feedback.

---

## 7. Success criteria

The MVP is "done" when all of the following hold for the three test AOIs:

- WoE posterior + LLM composite agree on tier for ≥75% of cells.
- ≥80% of training sites land in the **favorable** tier.
- ≤10% of cells in the Puget Lowland control AOI score above **negligible**.
- Full job for a 50 km² AOI completes in <90 seconds end-to-end.
- Every cell has at least 3 evidence bullets and at least 2 named data sources.
- Wilderness / tribal exclusions render correctly on the overlay.

---

## 8. Open questions to resolve before coding

These are the things I'd flag before Week 1 starts:

1. **Are placers in scope for v1**, or do we ship vein-only and add a placer model later? (Recommendation: vein-only for MVP — placers need NHD drainage + gravel-age data we haven't wired.)
2. **Training-set ground truth source.** Is Bulletin 1359 + DGER GM-22 + the MRDS dump enough, or do we want to OCR the WA Geology magazine production summaries (1989–1998) as well? (Recommendation: the former for v1.)
3. **What's our LLM budget per analysis job?** The 7-agent fan-out at 1.4 M precomputed cells is fine, but per-cell LLM evidence generation on a 50 km² AOI (~800 cells) at 6 agents = ~5k Sonnet calls. Worth caching by `(predictor_combination, mineral_target)` to keep $/job under $1.
4. **Do we want auto-recalibration** when new training sites are added (e.g., from user feedback) or stick with v1 weights for the MVP? (Recommendation: stick.)

---

## 9. References

- Boleneus, D.E., Raines, G.L., Causey, J.D., Bookstrom, A.A., Frost, T.P., Hyndman, P.C. (2001). *Assessment method for epithermal gold deposits in northeast Washington State using weights-of-evidence GIS modeling.* USGS OF 01-501.
- USGS Geological Survey Bulletin 1359 — Geology and Mineral Resources of the Northern Part of the North Cascades National Park.
- WA DGER GER GM-22 — Mineral Resource Maps of Washington State (`dnr.wa.gov/publications/ger_gm22_min_res_wastate.pdf`).
- WA DGER Geologic Information Portal (`geologyportal.dnr.wa.gov`).
- Existing project knowledge: `backend/app/agents/knowledge/lithology/gold.md`, `backend/app/agents/knowledge/historical/gold.md`, `.claude/skills/wa-tertiary-stratigraphy.md`, `.claude/skills/wa-pretertiary-basement.md`.

---

*Written May 2026. This plan replaces the "applies to NE WA epithermal" assumption baked into the original OF 01-501-derived design with a Cascade-vein-gold model. Update this file when the WoE weights are calibrated (end of Week 3) and again after the test-AOI validation (end of Week 5).*
