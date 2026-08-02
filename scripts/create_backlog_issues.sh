#!/usr/bin/env bash
#
# create_backlog_issues.sh — seed the GeoProspector backlog as GitHub Issues.
#
# Creates 6 epics and 20 subtask issues, plus the labels they use. Each epic
# body is rewritten at the end with a checklist linking its subtasks.
#
# Usage:
#   DRY_RUN=1 ./scripts/create_backlog_issues.sh      # print what would happen
#   ./scripts/create_backlog_issues.sh                # actually create them
#   REPO=owner/name ./scripts/create_backlog_issues.sh
#
# Requires: gh (authenticated). Safe to re-read, NOT idempotent for issues —
# running it twice creates duplicates. Labels are created idempotently.
#
set -euo pipefail

REPO="${REPO:-raghavmandadi1/prospector-ai}"
DRY_RUN="${DRY_RUN:-0}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Dry-run issue numbering. Kept in a file, not a variable: mk_issue is always
# called inside a command substitution, so a shell variable would be
# incremented in a subshell and discarded.
echo 1000 > "$TMP/counter"

log() { printf '%s\n' "$*" >&2; }

next_fake_num() {
  local n
  n="$(($(cat "$TMP/counter") + 1))"
  echo "$n" > "$TMP/counter"
  printf '%s' "$n"
}

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
preflight() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN — no gh calls will be made. Target repo would be: $REPO"
    return 0
  fi
  command -v gh >/dev/null 2>&1 || { log "ERROR: gh not found. brew install gh"; exit 1; }
  gh auth status >/dev/null 2>&1 || { log "ERROR: gh not authenticated. Run: gh auth login"; exit 1; }
  gh repo view "$REPO" >/dev/null 2>&1 || { log "ERROR: cannot access repo $REPO"; exit 1; }
  log "Preflight OK — creating issues in $REPO"
}

# ---------------------------------------------------------------------------
# mk_label <name> <color> <description>
# ---------------------------------------------------------------------------
mk_label() {
  local name="$1" color="$2" desc="$3"
  if [[ "$DRY_RUN" == "1" ]]; then
    log "  label: $name (#$color)"
    return 0
  fi
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" \
    --force >/dev/null 2>&1 || log "  WARN: could not create/update label $name"
}

# ---------------------------------------------------------------------------
# mk_issue <title> <labels-csv>   (body on stdin)
# echoes the new issue number on stdout; all logging goes to stderr
# ---------------------------------------------------------------------------
mk_issue() {
  local title="$1" labels="$2"
  local bodyfile="$TMP/body.$$.$RANDOM.md"
  cat > "$bodyfile"

  if [[ "$DRY_RUN" == "1" ]]; then
    local fake
    fake="$(next_fake_num)"
    log "  #$fake  [$labels]  $title"
    printf '%s' "$fake"
    return 0
  fi

  local url
  url="$(gh issue create --repo "$REPO" --title "$title" \
        --body-file "$bodyfile" --label "$labels")"
  local num="${url##*/}"
  log "  #$num  $title"
  printf '%s' "$num"
}

# ---------------------------------------------------------------------------
# mk_epic <key> <title> <labels-csv>   (body on stdin)
# <key> is a short handle (E1..E6) used by mk_child/finalize_epic. State is
# kept on disk rather than in shell variables so it survives the command
# substitutions mk_issue runs inside.
# ---------------------------------------------------------------------------
mk_epic() {
  local key="$1" title="$2" labels="$3"
  local bodyfile="$TMP/epic_${key}_body.md"
  cat > "$bodyfile"
  local num
  num="$(mk_issue "$title" "$labels" < "$bodyfile")"
  : > "$TMP/epic_${key}_children.md"
  printf '%s' "$num" > "$TMP/epic_${key}_num"
}

# ---------------------------------------------------------------------------
# mk_child <epic-var-name> <title> <labels-csv>   (body on stdin)
# ---------------------------------------------------------------------------
mk_child() {
  local epicvar="$1" title="$2" labels="$3"
  local epicnum
  epicnum="$(cat "$TMP/epic_${epicvar}_num")"
  local bodyfile="$TMP/child.$$.$RANDOM.md"
  {
    cat
    printf '\n---\nParent epic: #%s\n' "$epicnum"
  } > "$bodyfile"
  local num
  num="$(mk_issue "$title" "$labels" < "$bodyfile")"
  printf -- '- [ ] #%s %s\n' "$num" "$title" >> "$TMP/epic_${epicvar}_children.md"
}

# ---------------------------------------------------------------------------
# finalize_epic <epic-var-name> — append the subtask checklist to the epic
# ---------------------------------------------------------------------------
finalize_epic() {
  local epicvar="$1"
  local epicnum children final
  epicnum="$(cat "$TMP/epic_${epicvar}_num")"
  children="$TMP/epic_${epicvar}_children.md"
  final="$TMP/epic_${epicvar}_final.md"

  {
    cat "$TMP/epic_${epicvar}_body.md"
    printf '\n## Subtasks\n\n'
    cat "$children"
  } > "$final"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "  would update epic #$epicnum with $(wc -l < "$children" | tr -d ' ') subtasks"
    return 0
  fi
  gh issue edit "$epicnum" --repo "$REPO" --body-file "$final" >/dev/null
  log "  updated epic #$epicnum checklist"
}

# ===========================================================================
preflight

log ""
log "Creating labels..."
mk_label "epic"        "5319E7" "Umbrella issue tracking a body of work"
mk_label "grounding"   "B60205" "Making agent output traceable to real data"
mk_label "validation"  "0E8A16" "Tests, backtests, calibration"
mk_label "persistence" "1D76DB" "Saving and reloading runs"
mk_label "data-loader" "FBCA04" "Loading datasets already on disk"
mk_label "data-source" "D93F0B" "Acquiring new external datasets"
mk_label "ui"          "C2E0C6" "Frontend presentation and honesty"
mk_label "backend"     "BFD4F2" "Python / FastAPI"
mk_label "frontend"    "F9D0C4" "React / TypeScript"
mk_label "knowledge"   "8B5CF6" "Agent domain knowledge markdown"
mk_label "P0"          "B60205" "Blocks trusting any output"
mk_label "P1"          "D93F0B" "High leverage, not blocking"
mk_label "P2"          "FEF2C0" "Worth doing, not soon"

# ===========================================================================
# EPIC 1 — Grounding
# ===========================================================================
log ""
log "EPIC 1 — Grounding..."

mk_epic E1 "EPIC: Ground the agents in real spatial data" "epic,grounding,P0" <<'EOF'
## Problem

On the default dev path, every agent scores cells using nothing but the model's
own regional prior, and the UI presents the result as if it were evidence-backed.

Two independent failures compound:

1. `orchestrator._build_spatial_context()` opens a `try`, imports
   `app.db.session`, and dies on `ModuleNotFoundError: asyncpg` before the
   `Feature` model import is ever reached. `except Exception` swallows it and
   every agent receives the empty context dict. `asyncpg` and `geoalchemy2` are
   in `backend/requirements.txt` but **not** in `backend/requirements-dev.txt`,
   which is what `run-dev.sh` installs against.
2. Only 2 of 6 agents have a knowledge file (`lithology/gold.md`,
   `historical/gold.md`). There is no `default.md`. The other four run with
   `system=None` while carrying 0.60 of the gold weight — structure alone is
   the single highest at 0.30.

## Observed symptom

A 45-cell / 1000 m run over ~47.46N 121.41W produced a smooth NE-to-SW score
gradient whose own evidence strings read *"continuing northward"* and *"slight
confidence reduction ... northward and westward"* — a single generated
narrative applied across 45 cells, not 45 independent assessments. The evidence
also placed the cell in "Chiwaukum/Index Schist terrain" with "proximity to
Wenatchee Pluton to the east"; Chiwaukum Schist is mapped ~50-60 km NE and the
Wenatchee area ~80 km east. That is a hallucinated spatial claim presented at
1 km cell resolution.

Worse, the cell reported `data_sources_used: [usgs_mrds, historical_knowledge,
geological_knowledge]` while no database query had run at all.

## Definition of done

- Agents receive real records from PostGIS on the dev path, or fail loudly.
- No agent contributes weight to a composite without either retrieved records
  or a knowledge file — and the UI says which.
- `data_sources_used` reflects what was actually retrieved.
EOF

mk_child E1 "Fix dead spatial context on the DEV_MODE path" "backend,grounding,P0" <<'EOF'
`orchestrator._build_spatial_context()` never reaches its query. The `try`
block imports `sqlalchemy`, then `app.db.session`, then `app.models.feature`.
Import #2 raises `ModuleNotFoundError` because `db/session.py` calls
`create_async_engine()` at module scope with a `postgresql+asyncpg://` URL and
`asyncpg` is absent from `backend/requirements-dev.txt`. The
`except Exception` handler swallows it.

Note the fix is not "add geoalchemy2" — that import line never executes.
Order matters: `asyncpg` first, then `geoalchemy2`, then a populated DB.

### Tasks
- [ ] Add `asyncpg` and `geoalchemy2` to `backend/requirements-dev.txt`
- [ ] Make `db/session.py` lazy — engine created on first use, not at import,
      so a missing DB degrades predictably instead of at import time
- [ ] Correct the `DATABASE_URL` default: `config.py` says `localhost:5432`,
      Compose maps the host to **5433**
- [ ] Narrow the `except Exception` — distinguish "no DB configured"
      (expected, degrade) from "query failed" (bug, surface it)
- [ ] Populate the `spatial_context` `_error` key on failure so it reaches the
      run log rather than only stderr

### Acceptance criteria
- A dev run over an AOI containing known MRDS points emits a
  `spatial_context` event with non-zero `counts`
- `backend/tests/test_run_telemetry.py` currently asserts spatial context is
  **dead**; that assertion should now fail and must be inverted as part of
  this change
EOF

mk_child E1 "Derive data_sources_used from retrieved records, not from the LLM" "backend,grounding,P0" <<'EOF'
`data_sources_used` is entirely LLM-authored, and the prompts explicitly
instruct the model to fabricate it:

- `backend/app/agents/historical_agent.py:85` — *"data_sources_used should
  include ["historical_knowledge", "usgs_mrds"] when using your training data"*
- `backend/app/agents/lithology_agent.py:70` — *"data_sources_used should be
  ["geological_knowledge"] when using your training data"*

So a cell claims `usgs_mrds` as a source specifically when it used **no** MRDS
record. `base_agent.py:478` copies the string through verbatim. This is the
most misleading field in the product because it is the one a user would check
to decide whether to trust a score.

### Tasks
- [ ] Remove the `data_sources_used` instructions from both agent prompts
- [ ] Populate `data_sources_used` in `base_agent.py` from the record IDs
      actually passed into the batch prompt via `spatial_context`
- [ ] Add a distinct `model_prior` marker for scores with no retrieved backing
      — never a real dataset name
- [ ] Keep the knowledge-file name as a separate `grounding` field rather than
      laundering it into the source list

### Acceptance criteria
- A run with spatial context empty lists exactly `["model_prior"]`
- A run with retrieved MRDS points lists `usgs_mrds` and the drawer can show
  which specific records
EOF

mk_child E1 "Write knowledge/structure/gold.md" "knowledge,P0" <<'EOF'
Structure carries the highest gold weight (0.30 in `scoring/weights.py`) and
has neither a knowledge file nor any data. It is the single largest ungrounded
contribution to every gold composite.

### Content requirements
Follow the pattern set by `knowledge/lithology/gold.md`:
- WA-specific named structures, not global heuristics — Republic graben and its
  bounding faults, Toroda Creek and Keller grabens, Ross Lake fault zone,
  Straight Creek fault, Entiat fault, the Eocene extensional framework of NE WA
- A scoring rubric the model can apply directly against retrieved
  `fault` / `fold` / `dike` geometry: distance-to-fault bands, fault
  intersection density, dilational vs. compressive orientations, splay and
  jog settings
- Confidence-calibration guidance: what to score when structural data is
  present vs. absent — and an explicit instruction to return low confidence
  rather than invent a gradient when no structure data is supplied
- Common pitfalls: mapped-fault density is partly a mapping-effort artifact;
  1:24k coverage quality varies

### Acceptance criteria
- `agent_grounding` reports `structure/gold.md` with non-zero `knowledge_chars`
- Blocked in practice on the fault geometry from the surface-geology loader
EOF

mk_child E1 "Add a default.md guard so no agent silently runs with system=None" "backend,knowledge,P1" <<'EOF'
`BaseAgent.load_knowledge()` returns `None` when neither
`<domain>/<mineral>.md` nor `<domain>/default.md` exists, and `run()` proceeds
with `system=None`. Four of six agents are in this state and nothing in the
scoring path or UI treats them differently from a grounded agent.

### Tasks
- [ ] Write `knowledge/<domain>/default.md` for `geochemistry`, `proximity`,
      `remote_sensing` — minimum viable: WA context, a scoring rubric, and
      explicit "return low confidence when no data is supplied" guidance
- [ ] Add a config flag (`REQUIRE_GROUNDING`, default off) that makes an
      ungrounded agent return `AgentResult(status="failed")` instead of scoring
- [ ] Emit a job-level warning when `ungrounded_agents` is non-empty and their
      summed weight exceeds a threshold — `_roll_up_usage()` already computes
      the list

### Acceptance criteria
- No agent reaches `call_llm` with `system=None` unless `REQUIRE_GROUNDING` is
  off, and when it does the run log says so before results render
EOF

finalize_epic E1

# ===========================================================================
# EPIC 2 — Validation
# ===========================================================================
log ""
log "EPIC 2 — Validation..."

mk_epic E2 "EPIC: Validation harness — establish whether the scoring works at all" "epic,validation,P0" <<'EOF'
## Problem

There is no way to tell a working model from a broken one. `scoring/engine.py`
and `scoring/grid.py` have zero coverage. The only tests are two hand-run smoke
scripts (`backend/tests/test_run_telemetry.py`,
`test_run_cancellation.py`) covering telemetry and cancellation. No pytest, no
conftest, no CI. `npm run lint` fails outright — eslint is neither installed
nor configured.

The all-zero-scores bug in `.claude/mistakes-log.md` passed the current bar
("it ran without an exception") for weeks.

## The deeper issue

Saving more runs does not help, because a run carries no label. Fitting
anything to accumulated LLM output is distillation of a hallucination, and it
will look like progress because the outputs become more self-consistent.

What is needed is a **held-out validation set of known occurrences** and a
single number: what fraction of known deposits land in the top decile of
scored area. A random model captures 10% of deposits in 10% of area. Until
that curve is above the diagonal, no other improvement is measurable.

`data/raw/of00-495/` is a published weights-of-evidence dataset for exactly
this region — it is also the free statistical baseline the agent pipeline
has to beat.

## Definition of done

- `pytest` runs green in CI on every push
- A backtest command prints a success-rate curve against held-out occurrences
- A WofE / logistic-regression baseline exists to compare the agents against
EOF

mk_child E2 "Add pytest scaffolding and a CI workflow" "validation,backend,P0" <<'EOF'
### Tasks
- [ ] Add `pytest`, `pytest-asyncio` to `backend/requirements-dev.txt`
- [ ] `backend/tests/conftest.py` with fixtures: a synthetic AOI polygon, a
      deterministic grid, a stubbed `anthropic.AsyncAnthropic`
- [ ] Convert the two existing smoke scripts to pytest tests (keep them
      runnable standalone if that is still useful)
- [ ] `.github/workflows/ci.yml`: `pytest` + `npm run typecheck` on push and PR
- [ ] Either install and configure eslint or remove the broken `lint` script
      from `package.json` so it stops reporting a false failure

### Acceptance criteria
- `pytest backend/tests` passes locally and in CI with no live network and no
  Anthropic key
EOF

mk_child E2 "Unit-test scoring/engine.py and scoring/grid.py" "validation,backend,P0" <<'EOF'
The two modules that determine every number on the map have no coverage.

### engine.py
- [ ] `_weighted_mean` — correct normalization; missing-agent fallback to 1.0
      (`engine.py:159`, reached when `orchestrator.py:147` returns `{}`)
- [ ] `confidence=0.0` cells are excluded, never averaged in as zeros
- [ ] `normalize_relative` — `relative_score` and `percentile` on a known
      input vector; verify tier cutoffs at 0.90 / 0.65 / 0.35
- [ ] Uniform AOI (max == min) yields flat mid shading and **no** invented
      hotspot — this is a stated design property with no test behind it
- [ ] All-`confidence=0` input degrades sanely rather than producing scores

### grid.py
- [ ] `generate_grid` cell count and bbox coverage for a known polygon; UTM
      projection round-trip accuracy
- [ ] Coarsening loop respects `MAX_LLM_CELLS = 150`
- [ ] `interpolate_to_fine_grid` IDW: a fine cell colocated with a coarse cell
      centroid inherits that value; `parent_cell_id` is always set
- [ ] `MAX_DISPLAY_CELLS = 12000` cap holds

### Acceptance criteria
- Milestone M4 in `docs/03_implementation_plan.md` can finally be checked
EOF

mk_child E2 "Build a WA gold ground-truth occurrence set" "validation,data-loader,P0" <<'EOF'
A labelled positive set is the prerequisite for every other validation task.

Source: `data/raw/ger_portal_mines_minerals/WGS_Mines_Minerals.gdb` — WA-authored,
statewide, better positional accuracy than MRDS.

### Tasks
- [ ] Enumerate layers with `ogrinfo` and confirm the feature-class names
      (`Gold_Silver_Locations`, `Metallic_Mineral_Occurences`,
      `Mining_Districts_WA`, `IAML_Sites`)
- [ ] Extract gold occurrences to a versioned GeoJSON/Parquet under
      `data/validation/` with provenance and extraction date
- [ ] Tier the labels: past producer with recorded production > occurrence with
      assay > prospect with no assay. A prospect is a weak positive, not a
      positive — this mirrors the assay-primacy rule already in
      `knowledge/historical/gold.md`
- [ ] Define negatives honestly. Absence of a recorded occurrence is not a
      negative; it may be absence of exploration. Document the chosen
      convention (e.g. random background sampling) and its bias
- [ ] Fixed train/test split with a stored seed, and spatial blocking so a
      test occurrence is never within one grid cell of a training one

### Acceptance criteria
- A documented, reproducible file other tasks can import; a train occurrence
  can never leak into a test AOI
EOF

mk_child E2 "Implement a success-rate-curve backtest and a WofE baseline" "validation,P0" <<'EOF'
The scoreboard. Without it, "better analysis" is unfalsifiable.

### Tasks
- [ ] CLI: `python -m backend.scripts.backtest --mineral gold --split test`
      that runs the pipeline over AOIs containing held-out occurrences with
      those occurrences excluded from `spatial_context`
- [ ] Report: % of held-out occurrences captured vs. % of area flagged, at each
      tier cutoff; plot the curve against the random diagonal
- [ ] Report per-agent contribution — which agents move the curve, which are
      noise. This is the empirical answer to the weight table in `weights.py`,
      which is currently hand-set with no evidence
- [ ] Implement a plain weights-of-evidence or logistic-regression baseline on
      the same rasters (`data/raw/of00-495/`) and print both curves side by side
- [ ] Store every backtest result so the curve is trackable over time

### Acceptance criteria
- One command, one plot, two curves. If the agent pipeline does not beat WofE,
  that is a finding and it gets written down rather than worked around.
EOF

finalize_epic E2

# ===========================================================================
# EPIC 3 — Persistence
# ===========================================================================
log ""
log "EPIC 3 — Persistence..."

mk_epic E3 "EPIC: Persist runs to disk and capture human judgment" "epic,persistence,P1" <<'EOF'
## Problem

Nothing survives a reload. Under `DEV_MODE=true` only `analysis_dev.router` is
mounted, results are streamed on the POST response, and the frontend keeps them
in the Zustand store capped at 20 runs (`store/index.ts:176`). Refresh the tab
and every run is gone. A ~3.5 minute, real-dollar LLM run is discarded on a
browser refresh.

## Scope decision

Do **not** reach for the Celery + Postgres prod path just to get persistence.
That path also swaps the SSE transport and the API-key source and would drag in
scope. A local SQLite store written from `analysis_dev.py` gets reproducibility
and diffing for roughly a day of work.

## What persistence is actually for

Not training. A saved run has no label, so accumulated runs are accumulated
opinions. Persistence buys three things that do matter:

1. **Reproducibility** — diff two runs and know whether the change came from
   your edit or from model nondeterminism.
2. **Regression detection** — re-run a fixed AOI after a prompt or knowledge
   change and see the composite move.
3. **A place to attach human labels.** A geologist marking a cell "this is a
   logging road, not a working" is a real label. Those compound into the only
   proprietary dataset in this project.
EOF

mk_child E3 "Add a SQLite run store with a reproducibility manifest" "persistence,backend,P1" <<'EOF'
### Tasks
- [ ] SQLite store at `data/runs/runs.db` (gitignored), written from
      `backend/app/api/analysis_dev.py` — engine-agnostic, no PostGIS, no Celery
- [ ] Persist: full `final_scores`, all `agent_results`, and the complete SSE
      event stream. The event stream is where the batch-level telemetry lives
      and is the most useful part for debugging
- [ ] Manifest per run so two runs are comparable: AOI geometry hash, mineral,
      requested + effective resolution, agent set, weights, `MAX_LLM_CELLS`,
      model string, SHA of each knowledge file loaded, git commit, timestamp,
      token totals and `est_cost_usd`
- [ ] Never persist the Anthropic API key — it arrives in the request body on
      this path
- [ ] `GET /analysis-dev/runs` and `GET /analysis-dev/runs/{id}` to list and
      replay

### Acceptance criteria
- A completed run reloads byte-identical from disk with no LLM calls
- Changing only the knowledge file produces a different manifest hash
EOF

mk_child E3 "Load past runs from disk on app startup" "persistence,frontend,P1" <<'EOF'
`store/index.ts:176` caps history at 20 in memory. Past Runs in `AnalysisPanel`
should be backed by the store from the previous subtask instead.

### Tasks
- [ ] Fetch the run list on mount; drop the 20-run in-memory cap
- [ ] Replay a stored run into the map and EvidenceDrawer with no LLM call —
      re-viewing a run must be free and must be visibly marked as a replay
- [ ] Show the manifest in the UI: model, knowledge files, weights, cost.
      A run whose knowledge files differ from the current working tree should
      say so
- [ ] Side-by-side diff of two runs over the same AOI — per-cell composite delta
- [ ] Real delete that removes the row, not just the list entry

### Acceptance criteria
- Reload the page, every prior run is still listed and openable
EOF

mk_child E3 "Capture per-cell human feedback" "persistence,frontend,P2" <<'EOF'
The only labels in this project that no competitor can buy.

### Tasks
- [ ] In `EvidenceDrawer`, a confirm / reject / uncertain control plus a free-text
      note per cell
- [ ] Structured reject reasons — wrong geology, inaccessible or withdrawn land,
      already claimed, evidence is fabricated, cultural/artificial feature
      misread as a working
- [ ] Persist against the run manifest so a label is traceable to the exact
      model, prompt, and knowledge version that produced the score
- [ ] Export labelled cells for use in the E2 validation set
- [ ] Explicit "the evidence string is wrong" flag — with the hallucination rate
      this pipeline currently has, that is the highest-signal label available

### Acceptance criteria
- Labels survive reload and export to the same format the backtest consumes
EOF

finalize_epic E3

# ===========================================================================
# EPIC 4 — Local data loaders
# ===========================================================================
log ""
log "EPIC 4 — Local data loaders..."

mk_epic E4 "EPIC: Load the ~608 MB of WA data already sitting in data/raw" "epic,data-loader,P0" <<'EOF'
## Problem

The highest-value data in this project is already on disk and no module reads
it. Grepping `backend/` and `frontend/src` for
`of00|ger_portal|surface_geology|mines_minerals` returns zero hits. The only
code touching these paths is `scripts/convert_of00_495.sh`, an offline step.

Confirmed present in `data/raw/` (gitignored):

- `ger_portal_surface_geology_24k/WGS_Surface_Geology_24k.gdb` — statewide
  1:24k `fault`, `fold`, `dike`, `contact`, `geologic_unit_poly`
- `ger_portal_mines_minerals/WGS_Mines_Minerals.gdb` — `Gold_Silver_Locations`,
  `Metallic_Mineral_Occurences`, `Mining_Districts_WA`, `IAML_Sites`
- `of00-495/` — `newageol.e00`, `newafaul.e00`, `newafold.e00`,
  `newadike.e00`, plus `of00-495.pdf` and `appendix_A_raw.txt` containing the
  published weights-of-evidence contrasts

## Why this is P0 and ranked above acquiring anything new

The structure agent carries the top gold weight and currently sees zero faults.
Loading one geodatabase converts the single largest weight in the model from
narrative generation into measurement. Distance-to-fault and fault-intersection
density need no LLM at all.

Note: nothing here is a recurring sync. These are one-time loads, unlike the
WFS connectors in `pipeline/ingest.py`.
EOF

mk_child E4 "Loader for WGS_Surface_Geology_24k.gdb" "data-loader,backend,P0" <<'EOF'
### Tasks
- [ ] `ogrinfo` the .gdb and record the actual layer names and field schemas in
      `docs/` — do not code against the names in CLAUDE.md without checking
- [ ] `backend/app/connectors/wa_dnr_surface_geology.py` — one-time load, not a
      `CONNECTOR_REGISTRY` sync target
- [ ] Map `fault`, `fold`, `dike`, `contact`, `geologic_unit_poly` onto the
      `Feature` schema in `models/feature.py`; reproject everything to
      EPSG:4326
- [ ] Preserve the attributes that matter for scoring: fault type, dip,
      certainty, geologic unit code and age. A fault with unknown certainty is
      not the same evidence as a well-constrained one
- [ ] GiST index on geometry; verify bbox query latency at typical AOI size
- [ ] Extend `_build_spatial_context()` to populate `fault_traces` and
      `geology_units` from these records

### Acceptance criteria
- A dev run over an AOI with mapped faults shows non-zero `fault_traces` in the
  `spatial_context` event, and the structure agent cites specific named faults
EOF

mk_child E4 "Loader for WGS_Mines_Minerals.gdb" "data-loader,backend,P0" <<'EOF'
WA-authored and more positionally accurate than MRDS, which
`knowledge/historical/gold.md` already caveats.

### Tasks
- [ ] Enumerate and document the real layer names and schemas
- [ ] `backend/app/connectors/wa_dnr_mines_minerals.py`
- [ ] Load occurrences, historical mining districts, and IAML sites; keep
      production figures and assay/grade values as structured fields, not
      free text — assay primacy depends on them being queryable
- [ ] Deduplicate against MRDS. Two records for one mine will double-count as
      independent evidence; key on name plus proximity and keep both
      provenances on the surviving record
- [ ] Populate `known_deposits` and `historic_mines` in `_build_spatial_context()`

### Acceptance criteria
- The historical agent cites specific named mines with real production figures
  instead of "no documented gold prospects" from its prior
- Shares the extraction path with the E2 ground-truth set — build once
EOF

mk_child E4 "Write the usgs_of00_495 loader" "data-loader,backend,P1" <<'EOF'
`scripts/convert_of00_495.sh` already does the GDAL-in-Docker `.e00` →
EPSG:4326 conversion and works. Only the loader is missing. Integration plan is
in `docs/04_usgs_of00_495_dataset.md`.

Four ArcInfo GRID layers over the six 1:100k quadrangles that are the heart of
WA gold country — Colville, Chewelah, Republic, Nespelem, Omak, Oroville:
`newageol` (lithology, 50 m), `newafold` (folds, 50 m), `newafaul` (faults,
100 m), `newadike` (dikes, 200 m). Native CRS UTM 11N / NAD27 (EPSG:26711).

### Tasks
- [ ] `backend/app/connectors/usgs_of00_495.py` — one-time load
- [ ] Verify the NAD27 → WGS84 datum shift is applied, not just the projection
      change. Skipping it puts everything ~100 m off, which matters at 50 m
      raster resolution
- [ ] Sample raster values per grid cell into `spatial_context`
- [ ] Extract the published weights-of-evidence contrast tables from
      `appendix_A_raw.txt` / `of00-495.pdf` into structured form — these are
      calibrated favorability weights and they feed the E2 baseline directly

### Acceptance criteria
- An NE WA AOI receives per-cell lithology, fault, fold, and dike raster values
- The WofE contrast table is machine-readable
EOF

mk_child E4 "Compute derived structural metrics instead of asking the LLM to eyeball them" "data-loader,backend,P1" <<'EOF'
Once fault geometry exists, the structural controls on orogenic gold are
arithmetic. An LLM is the wrong tool for this and will be worse at it than
PostGIS.

### Tasks
- [ ] Per analysis cell, compute: distance to nearest fault, fault-trace
      density, fault-intersection count (intersections are the classic
      dilational trap), distance to nearest lithologic contact, dike density
- [ ] Compute in a projected CRS (UTM), not degrees — degree-based distance is
      wrong and the error varies with latitude
- [ ] Pass these as **numbers** in `spatial_context` so the agent reasons over
      measurements instead of generating a trend
- [ ] Caution in `knowledge/structure/gold.md`: mapped-fault density partly
      reflects mapping effort, not real structural density
- [ ] Sanity-check against the E2 backtest — if distance-to-fault alone beats
      the full six-agent composite, that needs to be known and said out loud

### Acceptance criteria
- Every cell in `spatial_context` carries numeric structural metrics, and the
  structure agent's evidence strings reference those numbers
EOF

finalize_epic E4

# ===========================================================================
# EPIC 5 — New data sources
# ===========================================================================
log ""
log "EPIC 5 — New external data sources..."

mk_epic E5 "EPIC: Acquire the missing data layers" "epic,data-source,P1" <<'EOF'
## Problem

Four connectors fetch live (`usgs_mrds`, `usgs_ngdb`, `macrostrat`, `mindat`).
Two are stubs returning `[]` (`blm_mlrs.py`, `glo_records.py`). Nothing supplies
geophysics, imagery, elevation, or land status — so the remote_sensing agent
scores imagery it has never seen and the proximity agent has no claim data.

Ranked by signal per unit of effort. Do EPIC 4 first: loading data already on
disk beats acquiring anything new.

### Also worth doing, not yet split out
- [ ] ASTER / Sentinel-2 alteration band ratios (iron oxide, argillic, phyllic)
      via Earth Engine or Planetary Computer — the remote_sensing agent
      currently has zero pixels
- [ ] Stream-sediment geochemistry beyond NGDB: NURE HSSR and the National
      Geochemical Survey, with catchment-based anomaly assignment (assign an
      anomaly upstream, not to the sample point)

## Note on scope

`usgs_mrds.py` has no pagination and a 1000-record cap, and the `usgs_ngdb`
`typeName` is unverified. Both should be checked before more sources are piled
on top.
EOF

mk_child E5 "Integrate Earth MRI aeromagnetic and radiometric data for NE Washington" "data-source,P1" <<'EOF'
The single most valuable dataset not currently in the project.

Faults and contacts appear as magnetic lineaments — this is how structure is
actually mapped below cover. Potassic alteration appears in the radiometric K
channel, and many gold systems are K-anomalous. Nothing else on the roadmap
gives any view of the subsurface.

USGS and the Washington Geological Survey have flown a high-resolution
helicopter magnetic + radiometric survey over the **Republic area, NE
Washington** under the Earth Mapping Resources Initiative — the same six
quadrangles as OF00-495 and the heart of WA gold country. Public.

### Tasks
- [ ] Confirm current release status and grid format for the Republic survey
      via the Airborne Geophysical Survey Inventory; note that Earth MRI
      releases roll out over time and more surveys were planned for 2026
- [ ] Also evaluate the older statewide/national merged magnetic anomaly
      compilation as lower-resolution fallback coverage outside the survey
      footprint
- [ ] `backend/app/connectors/usgs_earthmri_geophysics.py`
- [ ] Sample per analysis cell: total magnetic intensity, reduced-to-pole,
      analytic signal or tilt derivative for edge detection, and radiometric
      K / Th / U plus the K/Th ratio
- [ ] Write `knowledge/geophysics/gold.md` — or fold into
      `knowledge/structure/gold.md` — covering how to read a magnetic
      lineament and what a K anomaly does and does not imply
- [ ] Track whether the survey footprint covers the requested AOI and say so
      in the UI when it does not

### Acceptance criteria
- Cells inside the survey footprint carry numeric mag/rad values
- Measured against the E2 backtest curve, not by inspection
EOF

mk_child E5 "Use WA statewide lidar for lineaments and direct detection of historic workings" "data-source,P1" <<'EOF'
Probably the most differentiated idea in the project, and WA-specific in a way
that fits the current scope.

Two distinct uses of the bare-earth DTM from the Washington Lidar Portal
(`lidarportal.dnr.wa.gov`, WGS-disseminated point cloud, DEM, and hillshade):

1. **Structural lineament extraction** at a resolution finer than the 1 km
   analysis cells — genuinely independent of the mapped-fault layer, and not
   subject to its mapping-effort bias.
2. **Direct detection of historic workings.** Adits, prospect pits, placer
   tailings, hand-dug ditches, and shaft collars are unmistakable in bare-earth
   hillshade. Nobody has systematically mined WA lidar for this. It is
   observation of physical evidence that someone already dug there, which is
   the strongest single indicator in prospecting.

### Tasks
- [ ] Confirm coverage and download mechanics per AOI; handle projects flown in
      different years at different point densities
- [ ] `backend/app/connectors/wa_dnr_lidar.py` — AOI-scoped fetch, not a bulk
      statewide load
- [ ] Derive slope, curvature, and a hillshade lineament raster
- [ ] Prototype workings detection. Start with a hand-labelled set from a known
      district (Monte Cristo or Liberty/Swauk, where workings are documented
      and dense) before any model. Expect heavy false positives from logging
      roads, skid trails, railroad grades, and glacial features — a detector
      that cannot separate a skid trail from an adit is worse than nothing
      because it manufactures confident evidence
- [ ] Cross-check detections against the loaded IAML sites from EPIC 4 —
      that is a free precision/recall estimate

### Acceptance criteria
- A measured precision/recall on the hand-labelled district before this feeds
  any score
EOF

mk_child E5 "Unstub blm_mlrs.py and glo_records.py; add land status" "data-source,backend,P1" <<'EOF'
Both connectors are registered and syncable and `fetch()` returns `[]`.

Prospectivity you cannot legally stake is worth nothing. This may be closer to
the actual commercial value than any scoring improvement: "high potential AND
open to location" is a far sharper output than "high potential."

### Tasks
- [ ] Implement `blm_mlrs.py` — active and closed mining claims from BLM MLRS.
      Verify what the public API actually exposes before designing the schema
- [ ] Implement `glo_records.py` — GLO patents. `knowledge/historical/gold.md`
      already documents how to interpret these
- [ ] Add surface management: USFS / BLM / state / private / tribal, plus
      wilderness, national park, national monument, and withdrawn lands.
      Withdrawn land is a hard stop regardless of score
- [ ] Historical claim **density** as a proxy for where prospectors
      independently concentrated — a strong prior, and closed claims are
      arguably more informative than active ones
- [ ] Surface land status as a hard filter in the UI, distinct from the score.
      Do not blend it into the composite; a great target on withdrawn land is
      still a great target, it is just unavailable

### Acceptance criteria
- A cell's drawer shows surface manager, withdrawal status, and overlapping
  claims with dates
- The proximity agent receives real claim geometry
EOF

finalize_epic E5

# ===========================================================================
# EPIC 6 — UI honesty
# ===========================================================================
log ""
log "EPIC 6 — UI honesty..."

mk_epic E6 "EPIC: Stop the UI selling confidence and precision the pipeline does not have" "epic,ui,P0" <<'EOF'
## Problem

The interface is more confident than the model. Three specific misrepresentations,
all cheap to fix, all currently active:

1. **Grounded and ungrounded scores are styled identically.** A cell reads
   "58/100, confidence 63%" whether it is backed by retrieved records or by
   nothing at all. `_roll_up_usage()` already computes `ungrounded_agents` and
   `agent_grounding` already reports a null `knowledge_file` — the data reaches
   the frontend and is dropped on the floor.
2. **Interpolated cells are indistinguishable from scored cells.** Agents score
   a coarse grid capped at `MAX_LLM_CELLS = 150`; anything finer is IDW-filled
   by `interpolate_to_fine_grid()`. Those cells carry `parent_cell_id` and
   render exactly like real ones.
3. **The resolution selector sells precision that does not exist.** Offering
   100 m when analysis happened at 1 km or coarser advertises a resolution the
   pipeline never computed.

## Why this is P0

This is the cheapest epic here and it is the difference between a tool that is
wrong and a tool that lies. Everything else on the roadmap takes weeks; this
takes days and it is what makes the output safe to show another person.
EOF

mk_child E6 "Badge grounded vs. model-prior scores throughout the UI" "frontend,ui,P0" <<'EOF'
### Tasks
- [ ] In `EvidenceDrawer`, mark each agent row as grounded (knowledge file plus
      record count) or model-prior. The screenshot case — lithology 79, historical
      13, both prior-only, `usgs_mrds` listed as a source — must be unmistakable
- [ ] Show the retrieved record count per agent per cell. Zero records is the
      most important number on the panel
- [ ] Job-level banner in `ResultsOverlay` when `ungrounded_agents` is non-empty,
      stating the summed weight affected — 0.60 of gold weight in the default
      six-agent config
- [ ] Separate the two axes of confidence. Today's single "63%" conflates
      "the model is sure" with "the evidence is good". Show both, or relabel
      it honestly as self-reported
- [ ] Handle `confidence=0.0` cells distinctly — the LLM never scored them; they
      must not render as low-scoring cells
- [ ] Add the missing `useAnalysisRunner.ts` cases so grounding events stop
      falling through to "Unhandled event"

### Acceptance criteria
- A screenshot of a results panel makes it obvious, with no explanation, which
  scores are evidence-backed
EOF

mk_child E6 "Distinguish interpolated cells and stop over-offering resolution" "frontend,ui,P1" <<'EOF'
### Tasks
- [ ] Render IDW-interpolated display cells differently from analysis cells —
      cells with a `parent_cell_id` are inherited values, not measurements.
      Reduced opacity or a hatch, and never a crisper-looking boundary than the
      analysis grid
- [ ] In `EvidenceDrawer`, state plainly when evidence comes from the parent
      analysis cell rather than the displayed cell
- [ ] Grey out or warn on display resolutions finer than the effective analysis
      resolution. The `grid_info` event already carries both plus the coarsening
      factor and it is only surfaced when coarsening occurred
- [ ] Always show the effective analysis resolution next to the requested one.
      A user picking 100 m and silently getting 2 km analysis is the core
      misrepresentation here
- [ ] Draw the analysis grid as an optional overlay so the real sampling density
      is visible

### Acceptance criteria
- The map can no longer imply spatial detail finer than what was scored
EOF

finalize_epic E6

# ===========================================================================
log ""
log "Done."
if [[ "$DRY_RUN" == "1" ]]; then
  log "That was a dry run. Re-run without DRY_RUN=1 to create these in $REPO."
else
  log "View them:  gh issue list --repo $REPO --label epic"
fi
