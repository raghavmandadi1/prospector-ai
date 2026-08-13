# GeoProspector — Claude Project Context

> This file is automatically loaded by Claude Code at the start of every session.
> Keep it current as the architecture evolves. Update when: new agents/connectors are added,
> major decisions are made, or the stack changes.

---

## What This Is

**GeoProspector** is a multi-agent AI application for mineral prospecting. It ingests geological,
geochemical, remote sensing, and historical mining data from public APIs, then runs specialist
AI agents in parallel to score a user-drawn area of interest (AOI) on an interactive map.

The core output is a **scored, color-coded grid** with per-cell evidence drilldown — not a
generic heatmap. The design goal is that every score is backed by traceable evidence from
named data sources.

As of 2026-08-12 that is partly true, and the split matters. Every cell now carries real
per-cell evidence read off disk: recorded occurrences with WA DNR's own assay and production
flags, mining districts, abandoned workings, corroborated toponyms, and — inside the
OF-00-495 footprint — the published OF01-501 weights-of-evidence contrast for its rock unit.
All six agents have a knowledge file. **But mapped lithology and structure cover only part of
the state** (Known Gap #2b), and where they do not, those two agents are back on model prior
at 0.55 of the gold weight. **Read "Known Gaps" before trusting a score**, and read the
`coverage` block in the run record for the AOI in front of you — it says which evidence
actually covered that polygon rather than which artifacts happen to be installed.

## Scope

- **Geographic scope:** Washington State only — not a generic global tool. Knowledge bases,
  formation references, and named districts (Republic, Blewett, Monte Cristo, Buckhorn, etc.)
  are WA-specific.
- **Primary mineral:** Gold. The scoring engine (`weights.py`) supports five minerals
  (gold, silver, copper, uranium, lithium), but the agent knowledge base currently only
  covers gold. Other minerals fall back to the engine without specialist domain knowledge
  until per-mineral knowledge files are written.

---

## Known Gaps

Read this first. These are open issues, not TODOs someone will get to — they change how
you should interpret output today.

### 1. ~~Only 2 of 6 agents have a knowledge file~~ — CLOSED 2026-08-12

All six now have `knowledge/<domain>/gold.md`: `lithology` (21.7 KB), `historical` (36.3 KB),
`structure` (27.6 KB), `geochemistry` (20.1 KB), `proximity` (19.5 KB), `remote_sensing`
(16.0 KB). No agent runs with `system=None` for gold, and
`test_orchestrator_integration.py` asserts `agents_without_knowledge == []` so a file going
missing again is a test failure rather than a silent quality drop.

Still true, and still worth knowing: there is no `default.md` anywhere, and **only gold is
covered**. Ask for silver, copper, uranium or lithium and all six agents fall back to
`system=None` exactly as before. `resolve_knowledge_path()` looks for
`<domain>/<mineral>.md` then `<domain>/default.md`, so the cheapest fix for the other four
minerals is a `default.md` per domain that states what generalises.

### 2. ~~Spatial context is dead in `DEV_MODE`~~ — no longer load-bearing, 2026-08-12

**The PostGIS query is still dead in dev mode, and that no longer matters**, because it is
no longer the only source. `_build_spatial_context()` now reads local files first
(`app.spatial.local_store`) and merges PostGIS on top only where it is reachable. See
"Local spatial context" under Architecture.

The old diagnosis is still accurate about PostGIS itself and worth keeping, because someone
will eventually want the prod path to work: `db/session.py` calls `create_async_engine()` at
module scope with a `postgresql+asyncpg://` URL, `asyncpg` is deliberately not in
`requirements-dev.txt`, so `ModuleNotFoundError` is raised before the `Feature` import on the
next line ever runs. Adding `geoalchemy2` alone fixes nothing — you need `asyncpg` first,
then `geoalchemy2`, then a populated database.

What changed in behaviour:

- The failure is logged at INFO as "PostGIS spatial context unavailable … using local files
  only", not as a warning, because it is the expected dev state.
- `_error` is set **only when no source produced anything**, so it now means what it says.
- The `spatial_context` SSE event carries `sources` (which artifacts loaded) and `coverage`
  (how much of *this* AOI they actually cover). The run record stores both.

### 2b. The 1:24k geology does not cover most of the AOIs we care about — NEW

This is the significant new gap, and it is a property of the data, not the code.

`WGS_Surface_Geology_24k` is a mosaic of **342 published quadrangles**, not a statewide
layer, and its holes are badly placed for this project. Measured 2026-08-12 against
`benchmarks/labels.yaml`, polygon counts within ±0.06° of each AOI centre:

| AOI | label | 24k polys | 24k structures | OF-00-495 |
|---|---|---|---|---|
| monte_cristo | positive | 0 | 0 | — |
| silver_creek_mineral_city | positive | 0 | 0 | — |
| sunset_mine_index | positive | 0 | 0 | — |
| money_creek_miller_river | positive | 0 | 0 | — |
| sultan_basin | positive | 0 | 0 | — |
| **nf_snoqualmie_buena_vista** | positive | 0 | 0 | — |
| **lennox_creek_bear_creek** | positive | 0 | 0 | — |
| republic_eureka_gulch | positive | 0 | 0 | **`Eck`, contrast 4.55** |
| puget_lowland_glacial | null | 282 | 16 | — |
| snoqualmie_batholith_interior | null | 0 | 0 | — |
| hoh_accretionary_complex | null | 0 | 0 | — |

One of eleven AOIs has 24k coverage, and it is a *null* AOI. Nothing within ~16 km of Monte
Cristo. The two AOIs marked as the corridor the project is built around have nothing.
Coverage is concentrated in the Puget lowland: 38,060 of 82,692 polygons sit in the
-122° longitude band, 1,862 in -118°.

Consequences, in order:

1. **NE Washington is covered instead by OF-00-495**, which is the better source there
   anyway — it is the only dataset keyed to the published OF01-501 unit codes. Republic's
   centre lands on `Eck`, the highest-contrast unit in the study (4.55). `structure_agent`
   falls back to the OF-00-495 fault raster where 24k has no trace to measure, and 32 of 57
   cells in a Republic AOI get a fault from it.
2. **Western Washington still has no mapped lithology or structure.** For the priority
   corridor, those two agents are back on model prior — 0.55 of the gold weight. The
   occurrence, district, IAML and toponym evidence *is* statewide and does cover it, so
   those AOIs are far better served than before, just not on geology.
3. The obvious fix is WA DNR's **1:100,000 statewide surface geology**, a different
   download that is not in `data/raw/`. `macrostrat.py` is a working connector and could
   fill the same gap live, at lower resolution.

None of this is silent. `coverage` in the `spatial_context` event and
`inputs.spatial_coverage` in the run record report per-AOI polygon, structure, WofE,
occurrence and per-cell counts, and `local_store` logs a warning naming the mosaic when an
AOI falls in a hole. "The geology store is installed" and "this AOI has mapped geology" are
different claims and the code keeps them apart.

### 3. Tests — `engine.py`'s maths is covered now

`pytest` is installed and **159 tests pass**:

```bash
.venv/bin/python -m pytest backend/tests -q
```

They cover the fixed grid (`test_grid.py`), run records and the cell cache
(`test_run_record_and_cache.py`), the whole pipeline with a stubbed LLM
(`test_orchestrator_integration.py`), the toponym matcher (`test_toponyms.py`),
and TypeScript/Python projection parity (`test_grid_frontend_parity.py` — runs
the real `coords.ts` under `node --experimental-strip-types` and compares against
pyproj, so the map and the backend cannot silently disagree about which cell the
cursor is over).

Added 2026-08-12: `test_engine.py` (`_weighted_mean`, `normalize_relative`,
`synthesize` — including the uniform-AOI case that must not invent hotspots, the
tier boundaries at exactly 0.90/0.65/0.35, and the fact that the composite and
the mean confidence use *different* denominators), `test_local_store.py`
(metre-not-degree distances, per-cell facts reaching the cache key, the
`role: "truth"` invariant, graceful degradation when artifacts are absent), and
`test_field_pins.py` (a Google My Maps KML and a Gaia GPX of the same points
normalise identically).

`backend/tests/conftest.py` is new and worth knowing about: the suite used to
import `app` **by accident**, because pytest imports every test module during
collection and two hand-run scripts happen to do `sys.path.insert` at module
scope. Renaming either of those would have broken a dozen unrelated tests with a
`ModuleNotFoundError`. It also exports `PYTHONPATH` so the tests that shell out
to a subprocess work — `test_grid.py::test_cell_id_is_stable_across_processes`
had been failing on that for as long as it has existed, contrary to what this
file used to claim.

Two older standalone smoke scripts are still run by hand and need a live uvicorn:

- `test_run_telemetry.py` — stubs `anthropic.AsyncAnthropic`, runs a full job through a
  live uvicorn, and asserts the token ledger sums correctly, that a `max_tokens` stop
  reason is reported as a partial parse, and that ungrounded agents are identified.
- `test_run_cancellation.py` — holds fake LLM calls open, closes the HTTP stream mid-run,
  and asserts no call completes or starts afterwards.

Both also assert live that spatial context is dead (Known Gap #2) and that `structure` runs
ungrounded (Known Gap #1) — they will start failing when those are fixed, which is the point.

`npm run lint` still fails (eslint neither installed nor configured); use `npm run typecheck`.

### 3b. Benchmark ground truth — real workings now, and a baseline to beat

**`known_workings` is populated and verified for 8 of 11 AOIs**, derived by
`scripts/build_labels_workings.py` from `wa_occurrences.geojson` and filtered to
survey- and topo-grade positions only. `district_centroid`, `variable` and
`derived` positions are excluded — a district centre in a site's row would anchor
a confident metric about ground it says nothing about. Gating is per-AOI
(`workings_verified`), not the global `verified` flag, because these metrics read
working coordinates and the global flag is about `approx_center`, which remains
unchecked and is only used to match a run to an AOI.

**There is now a non-LLM number to beat.** `backend/app/scoring/wofe_baseline.py`
implements the published OF01-501 model, and `benchmark.py --wofe-only` scores the
labelled AOIs with no run records and no tokens:

```
republic_eureka_gulch  2436 cells scored, mean 0.610, sd 0.369
                       tracts: favourable 1421, permissive 289, non-permissive 726
                       29/29 known workings located
                       mean baseline percentile 0.857, 14 in the top decile
```

That is the bar. A composite that ranks real workings below the 0.857 the
published statistics achieve is not adding value over a lookup table, and the
harness now reports Spearman correlation between the LLM composite and the
baseline so agreement and disagreement are both visible.

**The baseline refuses all ten western Washington AOIs**, because OF01-501 was
fitted on 50 epithermal training sites in six NE Washington quadrangles and
extrapolating it would be worse than saying nothing. Every AOI in the priority
corridor is in that list — so for the ground the project cares most about there is
still no independent check, only the LLM.

Still open: **no control AOIs are selected** (`labels.yaml` asks for 4–6 — ground
comparable to the positives that was prospected without result, which is the most
diagnostic label and the one nobody else can source), and **no noise floor** has
been established (needs ≥2 runs of one AOI on one clean commit with
`CACHE_ENABLED=false`), so no LLM delta can yet be called an improvement rather
than nondeterminism. Also flagged by the label builder and needing a human:
`snoqualmie_batholith_interior` is labelled `null` but has two survey-grade gold
workings within 6 km, so the label is suspect rather than the data.

### 4. Stubs and unused infrastructure

- `blm_mlrs.py` and `glo_records.py` — `fetch()` returns `[]`. Registered and syncable;
  they just import nothing. Unpermitted-claims data therefore never reaches the proximity
  agent, which the knowledge file says so explicitly.
- **MinIO / boto3** — a compose service, four config settings, and a requirements entry.
  Zero usage in `backend/app`. Object storage is entirely aspirational.
- **LangGraph** — in `requirements.txt`, imported nowhere. Orchestration is plain
  `asyncio.gather`.
- `EQUAL_WEIGHTS` in `weights.py` is never referenced; the orchestrator falls back to
  `{}` and `_weighted_mean` defaults each missing agent to 1.0. Same behavior, different
  code path than the one previously documented here.
- ~~608 MB of WA DNR geodatabases referenced by no code~~ — **now read.** Three build
  scripts extract them; see "Local spatial context" under Architecture. Only the `contact`
  feature class (142,727 lines) is deliberately skipped as too heavy for its marginal value.
- **No export endpoint exists, in either mode.** Still the largest missing UI capability:
  a scored grid you cannot get out of the browser is hard to act on in the field.

### 5. ~~No occurrence data on disk, so toponym corroboration is inert~~ — CLOSED 2026-08-12

`data/reference/wa_occurrences.geojson` is built (4.2 MB, 3,314 features) from the WA DNR
geodatabase rather than from MRDS, so the MRDS **403 on tiled bbox requests** is irrelevant
to it. `local_store` passes the occurrence list into `toponyms_for_cells(...,
occurrences=...)`, so `_corroborate()` now returns real corroborated/uncorroborated verdicts
and `score_cap_for()` stops applying the uncorroborated cap to everything.

The WA DNR source turned out to be better than MRDS for more than availability — see
"Local spatial context" for the `ASSAYS` / `PRODUCTION` / `LOCATION_ACCURACY` fields, which
converted the assay-primacy rule in `knowledge/historical/gold.md` from an inference the
model had to make into a lookup.

`usgs_mrds.py` was also fixed while nearby, and it was worse than documented: `typeName=mrds`
does not exist on that service (it publishes `ms:mrds-high`), so `fetch()` was raising a JSON
decode error on *every* call rather than silently truncating — the missing pagination was the
second bug hiding behind the first. The service serves GML only, and Washington alone holds
16,499 records against the old `maxFeatures=1000` ceiling. See that module's docstring.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11 · FastAPI · async/await throughout |
| Agent orchestration | Plain `asyncio.gather` + `asyncio.Semaphore` — **no framework** |
| LLM | Anthropic `claude-sonnet-4-6` (hardcoded, `base_agent.py:248`) |
| Spatial Database | PostgreSQL 15 + PostGIS 3.4 (host port **5433**) |
| Task Queue | Celery + Redis — prod path only, bypassed when `DEV_MODE=true` |
| Object Storage | MinIO — *provisioned but unused, see Known Gaps* |
| Tile Server | Martin (Rust) — serves the `features` table as MVT |
| Frontend | React 18 + TypeScript · MapLibre GL JS · Zustand · Tailwind CSS |
| AOI drawing | `@mapbox/mapbox-gl-draw` + `@turf/area` (25 km² AOI minimum) |
| Build | Vite · Docker Compose (7 services) |

There is no agent framework. `langgraph` is in `requirements.txt` and imported nowhere —
don't write code assuming graph state, checkpointing, or conditional edges exist.

---

## Architecture: Three-Phase Pipeline

```
Phase 1 — Data Ingestion (background / scheduled)
  Connectors → fetch raw → normalize → upsert PostGIS

Phase 2 — Area Selection (on-demand)
  User draws AOI polygon → selects mineral target → triggers analysis job

Phase 3 — Multi-Agent Analysis
  Orchestrator → fan-out 6 specialist agents (asyncio.gather)
             → Scoring Engine (confidence-weighted mean)
             → Scored GeoJSON grid → map overlay
```

**Phase 3 runs on one of two paths, and they are not equivalent:**

| | `DEV_MODE=true` (default) | `DEV_MODE=false` |
|---|---|---|
| Router mounted | `analysis_dev.py` **only** | `channels`, `features`, `analysis` |
| Execution | in-process `asyncio.Task` | Celery task on the `worker` service |
| SSE transport | `asyncio.Queue` streamed on the POST response | Redis pub/sub on `job:{id}:events` |
| Anthropic key | sent in the **request body** from the UI | read from `.env` |
| Persistence | `data/runs/*.json` + `data/cache/cells.sqlite` (both modes) | also `analysis_jobs` table |
| `/channels`, `/features` | **404** — ChannelDashboard tab is broken | working |
| `/cache/*`, `/reference/*` | working — they read files, not Postgres | working |
| Spatial context | **local files (works)**; PostGIS merge fails and is logged at INFO | local files, plus PostGIS if populated |
| Stop / cancel | working — client aborts the fetch, generator polls `is_disconnected()`, task is cancelled | **not wired** — no cancel endpoint for the Celery path |

### Local spatial context — how evidence reaches the agents

`backend/app/spatial/` is the agents' evidence base. It exists because the honest
description of a run used to be *"Claude scoring grid cells from a 50 KB prose briefing"*:
`_build_spatial_context()` only ever tried PostGIS, that query dies on the dev path, and
every agent received an empty dict.

**Files, not a database.** `sqlite3`, `json` and `shapely` against artifacts on disk, so it
works identically in both modes. Everything is optional — a missing artifact costs a line in
a prompt and a greyed-out map toggle, never a traceback. `data/derived/` is gitignored, so
absent is the fresh-clone default.

**Per-cell, not AOI-wide.** This is the substantive change. The old context keys were flat
lists for the whole AOI, which asked the model to do the spatial join itself from a JSON blob
and a list of cell centres. It cannot do that reliably, and when it fails it fails silently —
by smearing one district's evidence across every cell in the polygon. The join now happens in
Python. `cell_facts[cell_id]` carries, per cell:

| key | contents | from |
|---|---|---|
| `geology` | units under the cell with area fractions, names, ages, lithologies | `wa_geology.sqlite` |
| `structures` | faults/folds/dikes within the 1,700 m WofE buffer, azimuths folded to [0,180), favourable-trend flag, in-cell fault intersections | `wa_geology.sqlite` |
| `wofe` | modal + favourable OF-00-495 unit, published contrast, fault/fold/dike codes | `of00495.sqlite` |
| `occurrences` | counts at 1/2/5 km, nearest and best-documented record, assay/production counts | `wa_occurrences.geojson` |
| `district` | district membership with production figures, and distance if just outside | `wa_mining_districts.geojson` |
| `workings` | IAML adits, shafts, dumps within 5 km | `wa_iaml.geojson` |
| `toponyms` | lexicon hits with corroboration verdicts | `gnis_wa.tsv` + lexicon |
| `field_observations` | **`role: "evidence"` pins only** | `data/user_sites/` |

`base_agent.cell_facts_block()` renders one line per cell against the batch labels
(`c1`, `c2`, …). Cells with nothing get `no data` rather than being omitted: an absent line
reads as an oversight, and a model filling a perceived gap with a plausible guess is the
behaviour this exists to stop.

**Distances are metres.** `spatial/geometry.LocalMetric` projects into a local
equirectangular metre frame pinned to the AOI centre. Degrees would rank north–south
neighbours ~1.4× further away than east–west ones at equal true distance; EPSG:5070 is
equal-*area* and distorts local distance by position. `matcher._km_between` now delegates
here so there is exactly one definition of distance in the codebase. Distances are measured
from the **cell polygon**, so 0 km means "inside this cell".

**What the WA DNR fields bought.** `ASSAYS` and `PRODUCTION` are explicit per-site flags
(649 and 450 of 1,467 gold/silver records), so the assay-primacy rule is a lookup rather than
an inference. `LOCATION_ACCURACY` is mapped to an `accuracy_class`, and the prompts act on
it: **917 of 1,467 records are "coordinate accuracy highly variable" and 24 are mining
district centroids**, so a `district_centroid` record is passed to the model explicitly
labelled as having no site position and is barred from anchoring a distance argument. The map
styles by the same field — an approximate location must not be drawn as a crisp dot.

**Novelty.** `orchestrator._attach_novelty()` puts `nearest_occurrence_km` and
`novelty` ∈ {`confirms`, `extends`, `lead`} on every scored cell (≤0.5 km / ≤2 km / beyond).
This is the point of putting known workings on the map: a hot cell on three recorded workings
is the model confirming known ground, and a hot cell with nothing recorded within two miles
is a lead. On a plain choropleth those look identical and mean opposite things. `novelty is
None` means **unknown**, not novel, and the UI renders nothing for it — with no occurrence
extract built, calling every cell a lead would turn a missing file into a prospecting signal.

**Cache correctness.** `BaseAgent._cell_context()` had to change: `cell_facts` is a dict and
the old implementation kept only list and str values, so per-cell evidence would have been
invisible to the cache key and every cell would have gone on serving the score it got before
it had any evidence. `test_local_store.py` pins this.

**The role invariant.** A `role: "truth"` field pin never reaches a prompt.
`load_user_sites()` defaults `roles=("evidence",)` so a caller that forgets to filter gets
the safe answer, `local_store._user_pins()` re-checks, and the run record logs
`pin_roles_active` so it can be audited after the fact. If the model is told "someone marked
this spot" and the benchmark then asks whether it ranked that spot highly, the answer is yes
by construction.

Building the artifacts (all read `data/raw/`, all idempotent):

```bash
.venv/bin/python scripts/build_reference_extracts.py all   # → data/reference/*.geojson
.venv/bin/python scripts/build_geology_store.py            # → data/derived/wa_geology.sqlite
.venv/bin/python scripts/build_of00495.py                  # → data/derived/of00495.sqlite
.venv/bin/python scripts/import_field_pins.py --help       # → data/user_sites/*.geojson
```

`GET /reference/layers` reports which exist, including `geology_store` and `wofe_store`,
which are not overlays — they back the per-cell evidence and the run log needs to be able to
say whether they were present.

### SSE event contract

Both paths emit the same JSON payloads (`{"event": "<name>", ...}`); dev sends them on the
POST response, prod over Redis pub/sub. The frontend maps every one of these to a run-log
line in `hooks/useAnalysisRunner.ts` — add a case there when adding an event, or it lands
in the `default` branch as "Unhandled event".

| Event | Emitted by | Carries |
|---|---|---|
| `started` | orchestrator | `job_id` |
| `grid_info` | orchestrator | display/analysis resolution, cell count (only when coarsened) |
| `spatial_context` | orchestrator | per-domain record `counts`, `sources` (artifacts loaded), `coverage` (what covers *this* AOI), `cells_with_facts`, plus `error` when no source produced anything |
| `agent_started` | orchestrator | `agent_id` |
| `agent_grounding` | **agent** | `knowledge_file` (null ⇒ ran with `system=None`), `knowledge_chars` |
| `batch_started` | **agent** | batch index/count, cell count, prompt chars |
| `batch_complete` | **agent** | tokens in/out, `duration_ms`, `cells_scored`/`cells_requested`, `parse_status`, `stop_reason`, response preview |
| `batch_failed` | **agent** | batch index, error string |
| `cache_status` | **agent** | cell cache `hits` / `misses` (only when there were hits) |
| `agent_complete` | orchestrator | status, cells scored, `knowledge_file`, `warnings`, `usage` |
| `usage` | orchestrator | job token totals, `est_cost_usd`, `by_agent`, `ungrounded_agents` |
| `results` | `analysis_dev.py` | `final_scores`, `agent_results` (dev path only) |
| `job_complete` / `error` | orchestrator | terminal |

Telemetry is best-effort: `BaseAgent._emit()` swallows emitter exceptions so a broken
stream can never fail a run. It re-raises `CancelledError` — that is the stop signal, not
a telemetry failure.

**Cost figures are local estimates**, computed from `MODEL_PRICING` in `base_agent.py`.
That table is hardcoded and will drift; it is not billing data.

Phase 1 (ingestion) is therefore unreachable in dev mode. The frontend only ever calls the
dev path: `AnalysisPanel` uses `runAnalysisDev`, and `analysisApi.createJob` /
`subscribeToJobEvents` / `featuresApi` in `api/client.ts` are dead code no component imports.

---

## Directory Map

```
prospector-ai/
├── CLAUDE.md                        ← you are here
├── .claude/
│   ├── commands/                    ← custom slash commands (agents & tools)
│   │   ├── debug.md                 ← /debug
│   │   ├── learn.md                 ← /learn
│   │   ├── clean.md                 ← /clean
│   │   ├── review.md                ← /review
│   │   ├── new-agent.md             ← /new-agent
│   │   └── new-connector.md         ← /new-connector
│   ├── skills/                      ← WA-specific geological reference skills
│   │   ├── wa-tertiary-stratigraphy.md      ← Weaver (1916) Tertiary units, W-of-Cascades
│   │   ├── wa-eocene-coal-fields.md         ← Newcastle/Renton/Green River/Centralia coal measures
│   │   ├── wa-pretertiary-basement.md       ← Old Metamorphic Series, Index granodiorite, Hoh fm
│   │   ├── wa-historical-geology-source.md  ← citation/nomenclature rules for Weaver (1916)
│   │   └── skill_creator.md
│   └── mistakes-log.md              ← running log of bugs & lessons learned
├── backend/
│   └── app/
│       ├── agents/                  ← specialist agents + orchestrator
│       │   ├── base_agent.py        ← abstract base: build_prompt(), call_llm(), parse_llm_response(), load_knowledge()
│       │   ├── orchestrator.py      ← fans out agents, collects AgentResult, calls scoring engine
│       │   ├── lithology_agent.py
│       │   ├── structure_agent.py
│       │   ├── proximity_agent.py
│       │   ├── geochemistry_agent.py
│       │   ├── remote_sensing_agent.py
│       │   ├── historical_agent.py
│       │   └── knowledge/           ← per-domain, per-mineral domain knowledge (markdown)
│       │       ├── lithology/gold.md      ← WA gold favorability by lithology + OF01-501 contrasts
│       │       ├── historical/gold.md     ← districts, production, assay-primacy keyed to DNR flags
│       │       ├── structure/gold.md      ← 1,700 m buffer, 345-030° trend, mapping-intensity caveat
│       │       ├── geochemistry/gold.md   ← no-samples IS the primary path; mineralogy as proxy
│       │       ├── proximity/gold.md      ← distance bands, position discipline, own circularity
│       │       └── remote_sensing/gold.md ← PREDICTED alteration only; confidence ceiling 0.3
│       │       (gold only — no default.md, so other minerals still run system=None)
│       ├── knowledge/toponyms/       ← versioned lexicons, hashed into run provenance
│       │   └── gold_wa.yaml          ← 5 tiers, incl. a MEASURED anti-signal list
│       ├── spatial/                  ← THE AGENTS' EVIDENCE BASE — files, never PostGIS
│       │   ├── geometry.py           ← LocalMetric: the one definition of distance
│       │   ├── occurrences.py        ← WA DNR mines + ASSAYS/PRODUCTION/accuracy, districts, IAML
│       │   ├── geology.py            ← 1:24k units/faults/folds/dikes (342-quad mosaic, has holes)
│       │   ├── wofe_grid.py          ← OF-00-495 on the ladder + published OF01-501 contrasts
│       │   ├── user_sites.py         ← field pins; enforces "a truth pin never reaches a model"
│       │   └── local_store.py        ← build_local_context() → per-cell facts + coverage
│       ├── toponyms/matcher.py       ← deterministic GNIS matcher, stream-aware, capped
│       ├── runs/record.py            ← RunRecorder: provenance, inputs, outputs, raw LLM
│       ├── cache/cell_cache.py       ← SQLite per-cell score cache
│       ├── connectors/              ← data source integrations
│       │   ├── base_connector.py    ← abstract base: fetch(bbox), normalize(raw)
│       │   ├── usgs_mrds.py         ← USGS MRDS via WFS — FIXED: real typeName, GML, paginated
│       │   ├── wa_dnr_minerals.py   ← WA DNR ArcGIS REST, paginated; for REFRESH, not the request path
│       │   ├── usgs_ngdb.py         ← USGS Geochemical DB — WORKING, typeName unverified
│       │   ├── macrostrat.py        ← Macrostrat geology formations — WORKING
│       │   ├── mindat.py            ← MinDat.org localities — WORKING (needs MINDAT_API_KEY)
│       │   ├── blm_mlrs.py          ← BLM federal claims — **STUB, fetch() returns []**
│       │   └── glo_records.py       ← BLM GLO patents — **STUB, fetch() returns []**
│       ├── pipeline/
│       │   ├── __init__.py
│       │   └── ingest.py            ← sync_channel(channel_id) Celery task + CONNECTOR_REGISTRY
│       ├── scoring/
│       │   ├── engine.py            ← synthesize(), normalize_relative(), tier thresholds
│       │   ├── grid.py              ← generate_grid(), interpolate_to_fine_grid() (IDW)
│       │   └── weights.py           ← mineral-specific default weight presets
│       ├── api/
│       │   ├── analysis_dev.py      ← DEV_MODE path: in-process run, SSE on POST + /cache/*
│       │   ├── reference.py         ← /reference/{layers,wilderness,toponyms,occurrences}
│       │   ├── channels.py          ← CRUD for data channel configs   (prod only)
│       │   ├── features.py          ← bbox-filtered GeoJSON feature query (prod only)
│       │   └── analysis.py          ← job submit / status / SSE via Redis (prod only; no export)
│       ├── models/
│       │   ├── feature.py           ← canonical geospatial feature schema
│       │   ├── channel.py           ← data channel config
│       │   ├── analysis_job.py      ← job status + results
│       │   └── agent_result.py      ← AgentResult + ScoredCell Pydantic models
│       ├── db/session.py
│       └── config.py
├── backend/tests/                   ← pytest (62 tests) + two hand-run smoke scripts
│   ├── test_grid.py                 ← fixed-grid acceptance criteria, statewide coverage
│   ├── test_run_record_and_cache.py ← cache hit/miss/invalidation, no-secrets, no relative fields
│   ├── test_orchestrator_integration.py ← whole pipeline with a stubbed LLM
│   ├── test_toponyms.py             ← lexicon, false friends, stream attribution, caps
│   ├── test_grid_frontend_parity.py ← coords.ts under node vs pyproj
│   ├── test_run_telemetry.py        ← hand-run: token ledger, parse health, grounding rollup
│   └── test_run_cancellation.py     ← hand-run: close the stream mid-run, assert LLM calls stop
├── frontend/
│   └── src/
│       ├── App.tsx                  ← flex shell; tab switcher is LOCAL state, not the store
│       ├── main.tsx
│       ├── components/              ← one .tsx per directory, except Map/
│       │   ├── Map/MapView.tsx      ← MapLibre + draw tool + choropleth + overlays
│       │   ├── Map/basemaps.ts      ← USGS services, per-service zoom limits, glyphs
│       │   ├── Map/coords.ts        ← EPSG:5070 + UTM + DMS parsing (mirrors grid.py)
│       │   ├── Map/drawStyles.ts    ← MapLibre-safe replacement for the draw theme
│       │   ├── Map/LayerPanel.tsx   ← basemap radio, results opacity, overlay toggles
│       │   └── Map/CoordinateReadout.tsx ← DD / DMS / UTM / cell id under cursor
│       │   ├── AnalysisPanel/       ← API key, AOI, mineral, weights, agent progress, Past Runs
│       │   ├── ResultsOverlay/      ← summary bar + legend + Relative/Absolute toggle
│       │   ├── ChannelDashboard/    ← channel list + sync (404s under DEV_MODE)
│       │   ├── EvidenceDrawer/      ← per-cell score breakdown sidebar
│       │   └── RunLog/              ← bottom console: live token ledger, event stream, Stop
│       ├── hooks/
│       │   └── useAnalysisRunner.ts ← owns run/stop; translates SSE events → log entries
│       ├── store/index.ts           ← single flat Zustand store, no middleware, no persist
│       ├── api/client.ts            ← typed API client (several exports are dead code)
│       └── types/index.ts
├── benchmarks/
│   ├── labels.yaml                  ← ground truth — verified: FALSE, see Known Gaps #3b
│   └── baselines/                   ← frozen benchmark results to diff against
├── scripts/                         ← everything here is offline and idempotent
│   ├── lib/e00.py                   ← pure-Python ArcInfo E00 GRID reader (no GDAL needed)
│   ├── build_reference_extracts.py  ← WA DNR mines/districts/IAML → data/reference/*.geojson
│   ├── build_geology_store.py       ← 24k surface geology → data/derived/wa_geology.sqlite
│   ├── build_of00495.py             ← OF-00-495 grids → data/derived/of00495.sqlite
│   ├── import_field_pins.py         ← KML/KMZ/GPX/GeoJSON → data/user_sites/, with roles
│   ├── benchmark.py                 ← offline harness over data/runs/
│   ├── build_gnis_extract.py        ← builds data/reference/gnis_wa.tsv
│   ├── convert_of00_495.sh          ← GDAL-in-Docker .e00 → GeoTIFF (superseded by lib/e00.py)
│   └── extract_pdfs.py              ← PDF triage + text extraction for docs/intake_analyses/
├── data/                            ← see data/README.md; raw/ is gitignored, now READ by scripts/
│   ├── reference/                   ← tracked, map-servable: gnis_wa.tsv, wa_wilderness.geojson,
│   │                                  wa_occurrences.geojson, wa_mining_districts.geojson, wa_iaml.geojson
│   ├── derived/                     ← gitignored, machine-built: wa_geology.sqlite, of00495.sqlite
│   ├── user_sites/                  ← gitignored: imported field pins (somebody's own GPS positions)
│   ├── literature/                  ← gitignored: the 28 source PDFs, extracted from the archive
│   ├── runs/                        ← gitignored: one JSON per analysis
│   └── cache/cells.sqlite           ← gitignored: per-cell score cache
├── tileserver/config.yaml           ← Martin; serves exactly one table (public.features)
├── run-dev.sh                       ← primary local dev path — no Docker, forces DEV_MODE=true
├── docker-compose.yml
├── docker-compose.dev.yml           ← OVERLAY — must be passed with -f alongside the base file
└── docs/
    ├── 01_system_design.md                ← authoritative architecture reference
    ├── 02_scaffold_prompt.md
    ├── 03_implementation_plan.md
    ├── 04_usgs_of00_495_dataset.md        ← NE WA W-of-E raster integration plan (designed, not built)
    ├── 05_western_wa_mvp.md
    ├── 05_knowledge_base_intake_2026-05-04.md
    ├── 06_data_sourcing_checklist.md
    ├── 07_stable_cell_ids.md              ← grid/cache/benchmark design + 2 spec departures
    ├── geoprospector_critique.md
    └── intake_analyses/                   ← per-source extracts from the scanned literature
```

---

## Key Patterns

### Adding a New Specialist Agent

1. Create `backend/app/agents/<name>_agent.py`
2. Subclass `BaseAgent` from `app.agents.base_agent`
3. Set `agent_id`, `agent_name`, `knowledge_domain` as class attributes
4. Implement `build_prompt(aoi_geojson, target_mineral, spatial_context) -> str` — this is
   the **only** required method. Every existing agent implements exactly this and nothing else.
5. Register in the `AGENT_CLASSES` **dict** in `orchestrator.py:40` (keyed by agent id)
6. Add a weight entry for the agent in every mineral preset in `scoring/weights.py`
7. Add the agent id to `AGENTS` in `AnalysisPanel.tsx` so its checkbox and weight slider appear
8. Write `knowledge/<domain>/<mineral>.md` — **not optional in practice.** Without it the
   agent runs with no system prompt while still contributing full weight (Known Gaps #1).

Do **not** override `parse_llm_response()`. The shared implementation handles all six
agents, including the truncated-JSON repair; overriding it loses that.

**Consume per-cell facts, not the AOI-wide lists.** The AOI-wide keys
(`geology_units`, `known_deposits`, …) are regional orientation and are capped at 40 records;
the evidence is `spatial_context["cell_facts"][cell_id]`. Write a module-level
`_render(facts) -> Optional[str]` for your domain and pass it to
`base_agent.cell_facts_block(...)`, which handles the batch labelling and emits `no data` for
cells your domain knows nothing about. Return `None` from `_render` for those cells rather
than a hedge — a rendered guess is indistinguishable from evidence once it is in the prompt.
Add whatever your domain needs to `local_store.build_local_context()`; do not query files from
inside a prompt builder, which runs once per batch.

`BaseAgent.run()` takes an optional `emit_fn` and emits `agent_grounding`, `batch_started`,
`batch_complete`, and `batch_failed` for you — a new agent gets run-log telemetry for free
as long as it does not override `run()`. If you need token counts inside a subclass, call
`call_llm_with_usage()`; `call_llm()` is a wrapper that discards them.

Use `/new-agent` command to scaffold the boilerplate.

### Adding a New Data Connector

1. Create `backend/app/connectors/<name>.py`
2. Subclass `BaseConnector` from `app.connectors.base_connector`
3. Implement `async fetch(bbox) -> List[Dict]`
4. Implement `async normalize(raw_records) -> List[Feature]`
5. Register key in `CONNECTOR_REGISTRY` in `pipeline/ingest.py`
6. Add a `Channel` seed record

Use `/new-connector` command to scaffold the boilerplate.

### Domain Knowledge Bases for Agents

Each specialist agent injects a Washington-specific, mineral-specific knowledge file as its
system prompt. This is how we ground the LLM in actual WA formations, districts, and
deposit models rather than generic global heuristics.

- Files live at `backend/app/agents/knowledge/<domain>/<mineral>.md`
  - `<domain>` is the agent name (`lithology`, `historical`, `structure`, etc.)
  - `<mineral>` is the lowercase target (`gold`, `silver`, `copper`, `uranium`, `lithium`)
- `BaseAgent.load_knowledge(domain, target_mineral)` loads the file for that combination,
  falling back to `<domain>/default.md` if the mineral-specific file does not exist,
  and returning `None` if neither is present.
- The loaded markdown is injected as the **system prompt** in the Anthropic API call, so
  the agent reasons with it baked into its persona — not as user-message context.

**Currently written — 2 files, covering 2 of 6 agents, gold only:**
- `lithology/gold.md` — WofE contrasts (USGS OF01-501), epithermal vs orogenic vs skarn
  scoring, NE WA grabens (Republic, Toroda Creek, Keller, First Thought), North Cascades
  metamorphic core
- `historical/gold.md` — district closure analysis (Republic, Monte Cristo, Blewett,
  Liberty/Swauk, Colville-Metaline), MRDS positional accuracy caveats, BLM/GLO claims
  interpretation, depth and technology modifiers

**Missing:** `structure/`, `geochemistry/`, `proximity/`, `remote_sensing/`, and any
`default.md`. Those four agents run with `system=None`. Highest-value next file is
`structure/gold.md` — structure carries the top gold weight (0.30) and has no grounding
at all. See Known Gaps #1.

**Pattern when adding a new knowledge file:** keep it WA-specific (cite real formations,
named districts, and named data sources), include a scoring rubric the LLM can apply
directly, and document confidence-calibration guidance and common pitfalls.

### Reference Skills (`.claude/skills/`)

Four Washington-geology reference skills, separate from agent knowledge files, encode
how to cite and interpret historical WA geological literature — primarily Weaver (1916,
WGS Bulletin 13). Agents producing evidence strings should reference these via the
Skill tool when their reasoning touches western WA Tertiary stratigraphy, Eocene coal
fields, or pre-Tertiary basement units. The `wa-historical-geology-source` skill defines
the canonical citation form (`Weaver_1916_WGS_Bulletin_13`) for `data_sources_used`.

### AgentResult / ScoredCell Schema

```python
AgentResult(
    agent_id="my_agent",
    status="completed",        # or "failed"
    scored_cells=[
        ScoredCell(
            cell_id="col_row",
            score=0.0–1.0,              # absolute composite, always preserved
            confidence=0.0–1.0,         # 0.0 means "LLM never scored this" — engine ignores it
            evidence=["Human-readable strings", ...],
            data_sources_used=["source_name", ...],
            # set by engine.normalize_relative() after synthesis:
            relative_score=0.0–1.0,     # min-max stretch within the AOI
            percentile=0.0–1.0,         # rank within the AOI
            tier="high|medium|low|negligible",
            # set by grid.interpolate_to_fine_grid() on IDW-downscaled cells:
            parent_cell_id="col_row",   # analysis cell this display cell inherited from
            # set by orchestrator._attach_novelty():
            nearest_occurrence_km=0.42,
            nearest_occurrence_name="Copper Key",
            novelty="confirms",         # confirms | extends | lead | None
        )
    ],
    agent_notes="optional summary string",
    warnings=[]
)
```

`confidence=0.0` is load-bearing: it is how a cell the LLM skipped is distinguished from a
cell the LLM scored as genuinely poor. Never let a parse failure emit `confidence>0`.

`novelty=None` is load-bearing in the same way: it means **unknown**, not novel. With no
occurrence extract built there is nothing to measure distance against, and rendering "lead"
in that case would convert a missing file into a prospecting signal. The UI must draw nothing
for `None`. Score and novelty are also separate visual channels by design — novelty drives an
outline treatment, never the fill ramp, because "how good" and "how new" are different
questions and merging them into one colour answers neither.

The frontend mirror lives in `frontend/src/types/index.ts` and must be kept in sync.

### Cell IDs are globally anchored — do not reintroduce AOI-relative ones

`scoring/grid.py` indexes cells off a **fixed grid** in EPSG:5070 (NAD83 / Conus
Albers), not off the AOI's bounding box:

```
wa5070-1000m-000349-000380     # <grid>-<resolution>-<col>-<row>
```

A given `cell_id` always names the same square of earth, which is what makes the
cell cache, run records and the benchmark possible. Three rules follow:

- **Resolutions must come from `RESOLUTION_LADDER`** `[125, 250, 500, 1000, 2000,
  4000, 8000]`. Each step is 2× the last and shares the origin, so cells nest as a
  quadtree and `parent_cell_id()` is exact containment. `snap_to_ladder()` coerces
  anything else; coarsening walks the ladder rather than multiplying by 2.
- **EPSG:5070, not UTM.** A single UTM zone cannot cover Washington — zone 10N ends
  at 120°W, and Republic, Metaline, Toroda Creek and Colville all sit east of it.
  Republic is the most-cited district in `knowledge/historical/gold.md`. AOIs
  outside `WA_BOUNDS` raise `AOIOutOfRangeError`. Full reasoning in
  `docs/07_stable_cell_ids.md`.
- **`geometry` is unclipped, `display_geometry` is clipped.** The canonical square
  is what gets cached and what the LLM reasons about; the AOI intersection is for
  rendering only. `engine.synthesize()` puts `display_geometry` on the ScoredCell.

`frontend/src/components/Map/coords.ts` reimplements the projection so the map can
show the cell id under the cursor. `backend/tests/test_grid_frontend_parity.py`
runs that TypeScript under node and compares against pyproj — if you change a grid
constant, change it in both places or that test fails.

### Prompts use short batch labels, not cell IDs

Canonical ids are 26 characters. Making the model retranscribe 50 of them per
call costs output tokens and invites digit errors that silently drop cells into
the zero-confidence fill path. `cell_summary()` emits `c1`, `c2`, … and
`parse_llm_response()` maps them back (it accepts canonical ids too). Nothing
outside the prompt ever sees a label.

### Scoring Tiers — AOI-Relative

Tiers and map shading are **relative to the AOI**, not absolute: the grid answers
"where are the best spots in THIS polygon", not "how does this area compare to
the world". After synthesis, `engine.normalize_relative()` annotates each cell
with `relative_score` (min-max stretch of the composite within the AOI),
`percentile` (rank within the AOI), and `tier` from percentile:

| Tier | Percentile within AOI |
|---|---|
| High priority | top 10% (≥ 0.90) |
| Medium priority | ≥ 0.65 |
| Low priority | ≥ 0.35 |
| Negligible | < 0.35 |

The absolute composite `score` is preserved on every cell; the UI legend has a
Relative/Absolute toggle and the EvidenceDrawer shows both. A uniform AOI
(max == min composite) gets flat mid shading — no invented hotspots.

### Two-Level Grid & LLM Batching

- Agents score a **coarse analysis grid** capped at `MAX_LLM_CELLS` (150) — the
  orchestrator doubles `resolution_m` (i.e. coarsens the grid, halving cell count each
  pass) until the AOI fits.
- If the user requested a finer display resolution (down to 100 m), coarse
  composite scores are IDW-interpolated onto the fine grid
  (`grid.interpolate_to_fine_grid`); fine cells carry `parent_cell_id` so the
  UI shows per-agent evidence from the analysis cell. The display grid is
  additionally capped at `MAX_DISPLAY_CELLS` (12,000).
- Within each agent, `BaseAgent.run()` scores cells in **batches of 50 per LLM
  call** (bounded concurrency), repairs truncated JSON responses, and fills
  LLM-missed cells with confidence=0 placeholders the engine ignores.
- Historical/geochemistry scoring follows **assay primacy**: records carrying
  assay/grade/production values dominate; district proximity without assay
  backing caps at ~0.6 (see `knowledge/historical/gold.md`).
- The UI keeps an in-memory **run history** (Past Runs in AnalysisPanel):
  old AOI polygons can be re-viewed and deleted after inspecting their data.

### Mineral Weight Presets (default)

Authoritative source: `backend/app/scoring/weights.py`. Weights are relative; the scoring
engine normalizes during weighted-mean computation. Minerals not listed get `{}` from
`orchestrator.py:147`, and `_weighted_mean` (`engine.py:159`) then defaults each agent to
1.0 — equal weighting. (`EQUAL_WEIGHTS` is defined in `weights.py` but never referenced;
same outcome, different path. Don't wire new code to it without also wiring the call site.)

| Agent | Gold | Silver | Copper | Uranium | Lithium |
|---|---|---|---|---|---|
| Lithology | 0.25 | 0.25 | 0.30 | 0.35 | 0.35 |
| Structure | 0.30 | 0.25 | 0.20 | 0.20 | 0.15 |
| Geochemistry | 0.20 | 0.20 | 0.25 | 0.25 | 0.25 |
| Historical | 0.15 | 0.15 | 0.03 | 0.10 | 0.03 |
| Remote Sensing | 0.07 | 0.05 | 0.15 | 0.03 | 0.07 |
| Proximity | 0.03 | 0.10 | 0.07 | 0.07 | 0.15 |

---

## Running the App

### Fastest path — no Docker (what you'll use day to day)

```bash
pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..

# Build the agents' evidence base from data/raw/ — ONE TIME, ~2 min total.
# Skip this and the run still works, but lithology, structure, historical and
# proximity fall back to model prior and the map's mine layers stay greyed out.
.venv/bin/python scripts/build_reference_extracts.py all
.venv/bin/python scripts/build_geology_store.py
.venv/bin/python scripts/build_of00495.py

./run-dev.sh          # forces DEV_MODE=true; starts uvicorn :8000 and vite :5173
```

Paste your Anthropic key into the AnalysisPanel field — in this mode the key travels in the
request body, not from `.env`. The Channels tab will 404 (see the DEV_MODE table above).

Check what the agents can actually see before spending tokens:

```bash
curl -s localhost:8000/api/v1/reference/layers | python3 -m json.tool
```

`geology_store` and `wofe_store` false means the derived stores are not built. And note the
difference between an artifact existing and it covering your AOI — the run log's
`spatial_context` line reports `coverage` for the polygon you actually drew (Known Gap #2b).

### Full stack — Docker Compose

```bash
# All 7 services: postgres, redis, minio, tileserver, backend, worker, frontend
docker-compose up

# Dev overlay — BOTH -f flags are required; docker-compose.dev.yml has no build context
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

# Run DB migrations (only meaningful with DEV_MODE=false — dev mode never touches the DB)
docker-compose exec backend alembic upgrade head

# Check PostGIS is live — service is named `postgres`, not `db`
docker-compose exec postgres psql -U geoprospector -d geoprospector -c "SELECT PostGIS_Version();"

# Tail logs — the Celery service is named `worker`
docker-compose logs -f backend
docker-compose logs -f worker

open http://localhost:8000/docs     # API docs
open http://localhost:5173          # frontend
```

### Gotchas that will waste your afternoon

- **Postgres is on host port 5433**, not 5432 (`docker-compose.yml:9`, to dodge a local
  clash). `DATABASE_URL` in `config.py` defaults to `localhost:5432` — running the backend
  outside Docker against the Compose DB needs the port corrected.
- `VITE_TILESERVER_URL` is **set nowhere**, so the Martin `features-points` layer never
  renders in any configuration. Set it if you want vector tiles.
- `npm run lint` fails — eslint is neither installed nor configured. Use `npm run typecheck`.
- **`bbox=` / `mask=` on either WA DNR geodatabase silently returns zero features.** The
  `.spx` spatial indexes are stale and OGR short-circuits to an empty result with no error, so
  it is indistinguishable from "no data in this area". The build scripts read whole layers.
- The 1:24k geology **has holes where you want it most** — no coverage at Monte Cristo,
  Sultan Basin, Lennox Creek or the North Fork Snoqualmie corridor. Known Gap #2b has the
  measured table. Do not read an empty `geology` fact as barren ground.
- There is no `ogr2ogr`, no GDAL CLI, no `geopandas` and no `pandas` in this environment.
  `pyogrio` (GDAL bundled in the wheel) is the way in, and `pyogrio.read_dataframe` raises
  because it wants geopandas — use `pyogrio.raw.read`, which returns a **4-tuple**
  `(meta, _, geometry_wkb, field_data)`.
- macOS has no coreutils `timeout`. Use `curl --max-time`.

---

## Development Conventions

### Python (backend)
- Async everywhere — all DB access and HTTP calls must use `async/await`
- Type hints on all function signatures
- Pydantic models for all API request/response bodies
- SQLAlchemy 2.0 style (`select()`, `session.execute()`, not legacy `.query()`)
- All geometries in SRID 4326 (WGS84) in the DB; use pyproj for UTM projections in grid math
- `logger = logging.getLogger(__name__)` at the top of every module
- Agents must never raise — catch exceptions in `run()`, return `AgentResult(status="failed")`

### TypeScript (frontend)
- Strict TypeScript — no `any` without a comment explaining why
- All API calls go through `src/api/` — no raw `fetch()` in components
- State in Zustand store — no prop drilling beyond 2 levels
- MapLibre layer IDs follow pattern: `<source>-<type>` (e.g., `mrds-points`, `results-fill`)
- Components are functional; hooks for logic

### Testing

```bash
.venv/bin/python -m pytest backend/tests -q      # 159 tests, ~30 s, no network, no API key
```

"It ran without an exception" is no longer the bar — that bar let the all-zero-scores bug in
`.claude/mistakes-log.md` live for weeks. `scoring/engine.py`, `scoring/grid.py`,
`spatial/`, the toponym matcher, the cell cache and the run recorder all have direct tests,
and `test_orchestrator_integration.py` exercises the whole pipeline against a stubbed LLM.

Two conventions in this suite that are load-bearing:

- **Canary tests are inverted, not deleted, when a gap closes.** The integration test used to
  assert `"structure" in agents_without_knowledge` as a deliberate tripwire on Known Gap #1.
  When the knowledge file landed it fired, and it was rewritten to assert
  `agents_without_knowledge == []` — so the gap reopening is still a failure.
- **A test that finds a bug asserts the current behaviour with a comment, and reports it.**
  Silently changing `scoring/engine.py` to make a test green is the exact move the mistakes
  log exists to prevent.

`npm run lint` still fails (eslint neither installed nor configured); use
`cd frontend && npm run typecheck`.

### Git
- Branch per feature: `feature/<slug>` or `fix/<slug>`
- Commit messages: `<type>(<scope>): <description>` (e.g., `feat(agents): add water chemistry agent`)
- Never commit `.env` or secrets
- Never commit binary GIS data or scanned PDFs — `data/raw/` and `data/literature/` are
  gitignored, and one `.e00` alone is 149 MB against GitHub's 100 MB hard limit

---

## Environment Variables

All config is loaded via Pydantic `BaseSettings` in `backend/app/config.py` from a `.env`
file. Variable names are case-insensitive. There are no raw `os.getenv()` calls in the
codebase — add new settings to `config.py` rather than reading env directly.

Copy `.env.example` → `.env` and fill in secrets before starting. **`.env.example` is the
source of truth** — it uses Docker service hostnames (`@postgres:5432`, `redis://redis:6379`,
`minio:9000`), which is what you want under Compose. Don't paste hostnames out of this file.

| Variable | Read by | Notes |
|---|---|---|
| `APP_ENV` | `config.py` | |
| `DEV_MODE` | `config.py:7` | defaults **true**; see below |
| `SECRET_KEY` | `config.py` | |
| `CORS_ORIGINS` | `config.py` | JSON list form: `["http://localhost:5173"]` |
| `DATABASE_URL` | `config.py` | `config.py` default says `localhost:5432`; Compose maps the host to **5433** |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | `config.py` | prod path only |
| `ANTHROPIC_API_KEY` | `config.py` | required for `DEV_MODE=false`; in dev the **UI** supplies it in the request body |
| `MINDAT_API_KEY` | `config.py` | required only for the mindat connector |
| `SAVE_RUN_RECORDS` | `config.py` | default **true** — one JSON per run in `data/runs/` |
| `SAVE_RAW_LLM` | `config.py` | default **true** — keep raw responses in the run record |
| `CACHE_ENABLED` | `config.py` | default **true**. Set false for benchmark noise-floor runs |
| `LOCAL_CONTEXT_ENABLED` | `config.py` | default **true**. Set false to reproduce the old "LLM regional knowledge only" runs — the honest way to measure what the data actually adds |
| `OCCURRENCE_SEARCH_RADIUS_KM` | `config.py` | default **5.0**. Also the radius beyond which a cell is called a `lead` rather than a re-find |
| `MAX_RECORDS_PER_CELL` | `config.py` | default **6**. Bounds prompt size: a cell in the Republic district can have dozens of occurrences and the marginal ones do not move the score |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` | `config.py` | **declared and read by nothing.** No boto3/S3 code exists in `backend/app`. |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | `docker-compose.yml` only | absent from `config.py` |
| `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY/ENDPOINT_URL` | **nothing** | absent from `config.py` |

The last two groups are silently dropped by `extra="ignore"` (`config.py:35`) — setting
them has no effect on the application.

### `DEV_MODE` behavior

`DEV_MODE=true` is the default in both `config.py:7` and `.env.example`, and `run-dev.sh`
forces it. It is not a minor variation on the prod path — see the comparison table under
Architecture. Summary of what it costs you:

- Only `analysis_dev.router` is mounted. `/channels` and `/features` 404.
- Nothing is persisted. Results exist only in the browser's Zustand store, capped at 20
  runs and lost on reload.
- The PostGIS spatial-context query fails silently; agents get no data (Known Gaps #2).

Always test with `DEV_MODE=false` before assuming a change works in production-like
conditions. The code paths genuinely differ — different router, different SSE transport,
different source for the API key.

---

## Claude Slash Commands

| Command | Purpose |
|---|---|
| `/debug` | Systematic debugging workflow for this stack |
| `/learn` | Record a bug or mistake into the lessons-learned log |
| `/clean` | Audit and remove dead code, unused imports, stale TODOs |
| `/review` | Code review with project-specific checklist |
| `/new-agent` | Scaffold a new specialist agent |
| `/new-connector` | Scaffold a new data connector |

Definitions live in `.claude/commands/`.

---

## Static Reference Datasets

**These are now read** — by the three offline build scripts, never at request time. Full
provenance in `data/README.md`. Reading them needs `pyogrio` (bundles GDAL; there is no
`ogr2ogr` and no Docker on a plain macOS box); the runtime needs only `sqlite3` and `shapely`
against their output.

One trap worth knowing before you touch either geodatabase: **`bbox` / `mask` spatial
push-down returns zero features on both of them** — the `.spx` indexes are stale, and OGR
short-circuits to an empty result rather than erroring. It looks exactly like "no data here".
The build scripts read whole layers for that reason.

- **WA DNR / WGS Mines & Minerals** (~77 MB) → `data/reference/*.geojson`.
  `Gold_Silver_Locations` (1,467), `Metallic_Mineral_Locations` (1,847),
  `Mining_Distircts_WA` (68 — the typo in the layer name is real), `IAML_Sites` (97),
  `IAML_Features` (359), and `Metallic_Minerals_Scanned_Documents` (107,739 rows joining
  sites to scanned source literature). Was the highest-value unused asset in the repo; the
  `ASSAYS` / `PRODUCTION` / `LOCATION_ACCURACY` fields are why it beats MRDS here.
- **WA DNR / WGS Surface Geology 1:24k** (~218 MB) → `data/derived/wa_geology.sqlite`
  (82,692 unit polygons, 12,416 faults, 3,350 folds, 2,467 dikes, 107 vents, 2,184 unit
  descriptions; `contact`'s 142,727 lines deliberately skipped). **A 342-quadrangle mosaic,
  not a statewide layer — read Known Gap #2b before relying on it.** Its unit labels are
  quad-local (`Evs(t)`, `Ev(p)`) and do **not** match the OF01-501 WofE codes; all six of
  `Evsf`, `Evst`, `Eck`, `Evkct`, `Evkf`, `Eco` are absent from its 2,186 distinct values, so
  do not try to match them by string similarity. It will look like it works.
- **USGS Open-File Report 00-495** (Boleneus & Causey 2000) — *Geologic raster data for
  weights-of-evidence analysis in NE Washington* → `data/derived/of00495.sqlite`
  (395,605 rows on 250 m cells of the fixed grid). Covers the six 1:100,000 quadrangles
  (Colville, Chewelah, Republic, Nespelem, Omak, Oroville) — the heart of WA gold country,
  and precisely where the 24k geology is thinnest. Four ArcInfo GRID layers: `newageol`
  (lithology, 50 m), `newafold` (folds, 50 m), `newafaul` (faults, 100 m), `newadike`
  (dikes, 200 m). Native CRS UTM 11N / NAD27 (EPSG:26711).

  **This is the only dataset on disk keyed to the published OF01-501 contrasts**, because its
  value-attribute tables carry the standardised Appendix A-1 labels. Favourable-unit cell
  counts as built: `Evsf` 10,554, `Evkf` 3,405, `Eco` 1,145, `Evkct` 464, `Eck` 427,
  `Evst` 274; 21,400 cells carry a fault code.

  Read by `scripts/lib/e00.py`, a pure-Python E00 GRID parser — the files are ASCII, five
  fixed-width integers per line, exactly `ncols × nrows` values terminated by `EOG`, with the
  VAT in the trailing `IFO` section. It asserts the value count and fails loudly, because a
  silent off-by-one there would shift every cell's geology by one pixel. This supersedes
  `scripts/convert_of00_495.sh` (GDAL-in-Docker), which still works but needs Docker.

  The fault and fold rasters are **sparse presence layers**, and their `.e00` VATs carry only
  VALUE and COUNT with empty labels — so the codes look opaque if you only read the raster.
  **They are not:** Appendices B-1 and B-2 of `of00-495.pdf` define every one, and
  `wofe_grid.FAULT_CODES` / `FOLD_CODES` transcribe them (7–10 thrust, 31/33 low-angle normal,
  43–45 normal; folds 1–3 anticline through 31–33 monocline). This distinction decides scores:
  the OF01-501 predictor is specifically a **normal** fault, a thrust is Mesozoic contraction
  pre-dating the Eocene ore event, and a low-angle normal fault is a core-complex detachment
  rather than a steep vein conduit. What the raster genuinely cannot give you is **orientation**
  — so the 345°–030° half of the OF01-501 rule is inapplicable there, and the prompt says so
  rather than letting the model assume the favourable case. Design notes in
  `docs/04_usgs_of00_495_dataset.md`.

---

## Current Implementation Status

Track progress in `docs/03_implementation_plan.md`.

- [x] **M1: Running scaffold** — 7 compose services defined; `run-dev.sh` boots without Docker
- [~] **M2: First data flowing** — **local data now reaches the agents** (`app/spatial/`);
      live connectors still only reachable on the prod path, the Martin tile layer never
      renders because `VITE_TILESERVER_URL` is unset, and `/features` 404s under DEV_MODE
- [~] **M3: Full data layer** — all 608 MB of local WA DNR / USGS data is loaded and served;
      `usgs_mrds` fixed; `wa_dnr_minerals` added for refresh. Still missing: `blm_mlrs` +
      `glo_records` are stubs, no geochemical sample source on disk, and no mapped geology
      west of the crest (Known Gap #2b)
- [x] **M4: Scoring foundation** — fixed, globally-anchored quadtree grid; `engine.py`'s
      weighted mean and relative normalization now have direct unit tests
- [x] **M5: First end-to-end analysis** — full job runs draw → agents → synthesis → grid
- [x] **M6: Full UI** — draw → run → results → evidence drawer, plus Relative/Absolute
      toggle, Past Runs, and the RunLog console (live token/cost ledger, per-batch event
      stream, grounding readout, Stop)
- [~] **M7: Production-ready MVP** — runs now persist (`data/runs/`), scores cache
      across runs, and `scripts/benchmark.py` exists; still no export endpoint, no CI,
      no working lint, unverified benchmark ground truth (#3b), four agents ungrounded
      (#1), spatial context dead in dev (#2), and no occurrence data (#5)

`[~]` = partially done.

---

*Last updated: 2026-08-12 — **connected the data to the agents.** The 608 MB in `data/raw/`
had never been read by any code, `_build_spatial_context()` died on the `asyncpg` import on the
only path anyone runs, and four of six agents had no knowledge file — so every score was model
prior plus a 50 KB markdown briefing. Now: three offline build scripts extract the WA DNR mines
and 1:24k geology geodatabases and the USGS OF-00-495 grids (the last via a pure-Python E00
reader, no GDAL); `app/spatial/` assembles them into **per-cell** evidence read straight off
disk, so the spatial join happens in Python instead of in the model's head; all six agents have
a gold knowledge file; the WA DNR `ASSAYS`/`PRODUCTION`/`LOCATION_ACCURACY` fields turned the
assay-primacy rule from an inference into a lookup; toponym corroboration works for the first
time; known mines and districts are on the map with uncertainty halos scaled to each record's
positional accuracy; every scored cell carries a `novelty` flag so a re-discovery is
distinguishable from a lead; field pins import from KML/GPX with a `role` field that keeps
`truth` pins away from the model. 62 tests → 161. Known Gaps #1 and #5 closed, #2 defanged, #3
closed. **New Known Gap #2b**, and it is the important one: the 1:24k geology is a
342-quadrangle mosaic and covers exactly one of the eleven benchmark AOIs, so west of the crest
lithology and structure are still ungrounded — but the run now says so instead of looking
normal.*

*Previously, 2026-08-01 (second pass) — implemented Workstreams A, B and C of
"steps for raghav". Grid rewritten onto fixed EPSG:5070 cell ids with a nesting
resolution ladder; run records, a SQLite cell cache and an offline benchmark
harness added; USGS basemaps, orientation controls, a layer panel and reference
overlays added to the map; a deterministic GNIS toponym matcher added. 62 pytest
tests where there were none. Two deliberate departures from that spec — the
analysis CRS and the default basemap — are argued from measurements in
`docs/07_stable_cell_ids.md`. Known Gaps #3b and #5 are new and both block
trusting benchmark output.*

*Previously, 2026-08-01 — documentation audited line-by-line against the source and
corrected. Removed the LangGraph claim (never imported); documented the DEV_MODE vs prod
split and `analysis_dev.py`; marked `blm_mlrs`/`glo_records` as stubs and MinIO/boto3 as
unused; recorded that only 2 of 6 agents have knowledge files and that the PostGIS
spatial-context query is dead in dev mode; fixed compose service names (`worker`,
`postgres`), the 5433 host port, and the dev-overlay invocation; documented `run-dev.sh`,
`scripts/`, and the unused WA DNR datasets; replaced `OrchestratorAgent.agents` with
`AGENT_CLASSES`. Verified as still accurate: the full 5-mineral weight table, the percentile
tier thresholds, MAX_LLM_CELLS/MAX_DISPLAY_CELLS/batch constants, and the AOI-relative
scoring description.*
