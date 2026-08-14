# Geology source for the NF Snoqualmie / Buena Vista corridor

**Date:** 2026-08-13
**Status:** decided — adopt 1:100k for the corridor; demote §47.1 from dominant
**Context:** Phase 0 spike from "Steps for Raghav 3.0", §46.3 / §47.1 / §48.3

`steps for raghav 3.0.md` §46.3 makes WA DNR **1:24k** Surface Geology the dominant input to
§47.1 (bedrock proximity, the highest-weighted pan-point criterion) and the sole input to
§48.3 (the glacial limit proxy), and §51 scopes the work to the whole corridor.

That cannot work, and §51's phase 6.0 ("smoke test 1:24k on one creek, 2 hours") is
**unexecutable** — there is no creek in the corridor with any 24k coverage to test.

This spike asked two questions separately, because they have different bars and — as it turns
out — different answers.

---

## The measurements

All figures over the corridor bbox `(-121.85, 47.45, -121.25, 48.15)`, measured 2026-08-13.

### Coverage — the same lattice test, both sources

A 0.02° lattice (1,085 sample points), counting points covered by at least one map-unit
polygon:

| Source | Covered | Faults | Folds | Contacts |
|---|---|---|---|---|
| **1:24k** (`data/derived/wa_geology.sqlite`) | **140 / 1085 = 13%** | 0 | 0 | n/a (skipped at build) |
| **1:100k** (GeMS service) | **1085 / 1085 = 100%** | **309** | 24 | 3,449 |

The 24k's 13% is concentrated in the low-elevation western margin — the covered quads are
Lake Chaplain, Lake Joy, North Bend, Snoqualmie and Sultan. The mineralised high country
(Lennox Creek, Bear Creek, Money Creek, upper NF Snoqualmie) has **nothing**. In the
47.4–48.2° band every published 24k quad west of the crest terminates at lon −121.7512.

This is a property of the source geodatabase's published-quad footprint, not of
`scripts/build_geology_store.py`. Rebuilding cannot fix it.

### Positional resolution — 144,550 boundary segments measured

| Metric | Value |
|---|---|
| Vertex spacing along unit boundaries, p10 | **26.5 m** |
| Vertex spacing, **median** | **70.5 m** |
| Vertex spacing, p90 | 179.3 m |
| Polygon area, median | 227,285 m² (≈ **477 m** square side) |
| Polygons smaller than 100 m × 100 m | 73 / 1,661 |

### Lexicon join — the research claim was overstated

The claim that "every Quaternary unit code joins the `unit` lexicon already on disk" is only
partly true:

| Join method | Features joined |
|---|---|
| Raw `MAP_UNIT_100K` code | 601 / 1543 = **39.0%** |
| Parenthetical suffix stripped (`KJmm(wa)` → `KJmm`) | 899 / 1543 = **58.3%** |

128 distinct codes; 61 bedrock codes with quad-local suffixes never resolve. And **`wtr`
(water) is 390 features — 25% of every polygon in the corridor** — with no lexicon entry at
all, plus `ice` at 19.

**This does not block anything**, because §47.1 does not need the lexicon. It needs a binary
classification, and the code *prefix* supplies that at 100% coverage:

| Class | Features | Share |
|---|---|---|
| Bedrock | 695 | 45.0% |
| Quaternary surficial | 439 | 28.5% |
| Non-land (`wtr` / `ice`) | 409 | 26.5% |

`wtr` must be an explicit **WATER** class, not an unknown. A quarter of the corridor's
polygons are water, and a reach flowing through one is a lake — not bedrock, and not
pannable.

### §48.3 glacial vocabulary — present and sufficient

247 glacial polygons after suffix stripping: `Qad` 116 (alpine glacial drift), `Qgo` 53
(recessional outwash), `Qgt` 52 (lodgment till), `Qgl` 16 (glaciolacustrine), `Qga` 10.
Against 192 other Quaternary. The glacial limit proxy is derivable.

---

## Answers

**Q1 — Is 1:100k good enough for the Workstream 5 sweep? YES, emphatically.**

Lithology and structure reason over 1000–2000 m analysis cells. A 70 m median boundary
resolution is 1/14 to 1/28 of a cell edge — far below the scale at which those agents make
distinctions. And the comparison is not "100k vs 24k", it is **100k vs nothing**: the corridor
currently has zero mapped units and zero structural lines, so both agents (0.55 of the gold
weight) run on model prior over the entire priority corridor.

309 faults where there were none is the single largest quality improvement available to the
`structure` agent, which carries the highest individual gold weight at 0.30.

**Q2 — Is 1:100k good enough for §47.1 creek-scale bedrock proximity? NO, not as specced.**

§47.1 proposes a 25 m buffer. That is **below the p10 vertex spacing (26.5 m)** and about a
third of the median (70.5 m) — i.e. finer than the smallest detail the source carries. A
buffer that tight is measuring digitising noise, not geology.

At 1:100k, §47.1 is a **valley-scale** discriminator ("is this reach in mapped bedrock or in
mapped valley fill"), not a reach-scale one.

---

## Decisions

1. **Adopt 1:100k as the corridor geology source**, as a fallback beneath 1:24k — 24k stays
   preferred where it exists (it is genuinely better, and it covers Republic-adjacent ground
   the sweep will also want). Wiring is contained: `local_store.py` calls
   `geology_mod.get_store()` at five singleton sites and `GeologyStore.__init__` already
   takes an optional path, so a store built with the 24k table and column names needs no new
   reader code.

2. **Build it before the corridor sweep, not inside Workstream 6.** Sweeping first means
   paying for LLM calls over ground lithology and structure cannot see, and writing those
   scores permanently into `data/cache/cells.sqlite` where later runs serve them as hits.

3. **Demote §47.1 from dominant.** Raise the buffer to **≥100 m** (median spacing plus
   margin) and shift weight onto the genuinely reach-scale lidar-derived terms — §47.2
   gradient and §47.5 confinement, which work at 1–3 m. §47.0 explicitly anticipates this
   ("bedrock proximity is a starting guess, not a settled answer"). Ship both presets
   (`spec_default`, `coverage_adjusted`) so the choice is a config flip and a diff.

4. **Classify by code prefix, not by lexicon join.** Use the lexicon opportunistically to
   enrich evidence strings with age and lithology names, and never let a failed join
   downgrade a cell to "unknown" — 42% of features would be lost that way. Strip the
   parenthetical suffix before joining. Treat `wtr` and `ice` as explicit non-land classes.

5. **The creek-scale question stays open and is now Workstream 6's, not this spike's.**
   Whether a ~70 m line is usable against a real channel can only be settled against a lidar
   hillshade, which needs the DEM from phase 6.0.5. Until then §47.1 output is a hypothesis
   to be field-checked — which §46.3 already said, and is still the right posture.

---

## What this does not fix

Known Gap #2b is narrowed, not closed. The corridor gets mapped geology for the first time,
but at 1:100k. `coverage` in the `spatial_context` event must report **which source** covered
each AOI, not just that geology was present — "this AOI has 24k geology" and "this AOI has
100k geology" are different claims and the run log has to keep them apart, exactly as it
already keeps "installed" and "covers this polygon" apart.
