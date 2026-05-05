---
name: wa-tertiary-stratigraphy
description: Reference for the Tertiary stratigraphy of western Washington as established by Weaver (1916, WA Geological Survey Bulletin 13). This skill should be used whenever an AOI falls in western Washington (west of the Cascade crest, between the Columbia River and the Strait of Juan de Fuca) and the lithology, structure, or proximity agents need to identify, correlate, or describe Eocene–Miocene sedimentary and volcanic units. Use to map informal/historical formation names to modern usage, to identify host-rock context for mineralization or petroleum, and to look up type sections, lithologic descriptions, and structural setting of named units.
source: Weaver, C.E. (1916). The Tertiary Formations of Western Washington. Washington Geological Survey Bulletin No. 13. 327 pp.
---

# Western Washington Tertiary Stratigraphy

Use this skill to ground the lithology_agent and structure_agent in the named Tertiary units of western Washington as originally defined by Weaver (1916). Many of these names are still in active use (e.g., Crescent basalts, Lincoln/Blakeley faunal zones, Puget Group equivalents), and historic literature uses Weaver's nomenclature directly.

## Geographic Scope

The bulletin covers the area from the western foothills of the Cascade Range west to the Pacific Ocean, and from the Columbia River north to the Strait of Juan de Fuca. The central Olympic Peninsula was excluded (only reconnaissance-level coverage). Always confirm the AOI overlaps this footprint before relying on these unit descriptions.

## Tertiary Stratigraphic Column (Weaver 1916, west of the Cascade crest)

Lower → Upper:

1. **Pre-Tertiary basement** (see the `wa-pretertiary-basement` skill): Old Metamorphic Series, Index granodiorite, Hoh formation.
2. **Eocene (Tejon equivalent, Upper Eocene dominant)** — the thickest and most economically important Tertiary section. Mixed marine, estuarine, and lacustrine sediments interbedded with basalt/andesite flows and tuffs.
3. **Oligocene** — chiefly marine sandstones and shales (~15,000 ft aggregate). Defined by faunal zones rather than discrete formations.
4. **Miocene** — Lower Miocene marine + Upper Miocene marine (Astoria-equivalent). Includes Snoqualmie granodiorite (intrusive) and the Enumclaw volcanic series.
5. **Pleistocene** glacial drift — blankets large portions of the Puget Lowland and obscures the underlying Tertiary section.

## Eocene Formations — Areas Defined by Weaver

Weaver subdivided the Eocene by geographic area rather than by formal formation names (formations were not yet stable across the region). Each area has its own lithology, thickness, and structure. When the orchestrator dispatches an AOI in any of these areas, mention the area name in the agent's evidence string so users can cross-reference the bulletin.

| Area (Weaver name) | Counties | Key lithology | Notes |
|---|---|---|---|
| Newcastle Hills – Grand Ridge | King | Coarse arkosic sandstones, shales, carbonaceous seams over basalt/andesite | Major coal field; Newcastle–Issaquah anticline. ~2,000 ft sediment over Eocene basalt. |
| Green River | King | Bayne / Franklin / Kummer members; sandstones, shales, coal | ~8,000 ft section. Type locality for Franklin sandstone. Coal seams up to 50 ft. |
| Cedar Mountain | King | Sandstones, shales, carbonaceous beds; lithologically similar to Renton | Lower 800 ft contains coal seams. |
| Raging River | King | Sandstones + shales with low-grade coal, intruded by andesite/rhyolite dikes | Intense dike intrusion has disrupted coal seams (e.g., Taylor mine). |
| Quimper Peninsula | Jefferson | Marine + volcanic Eocene | See dedicated map (Plate IV-C). |
| Ilwaco | Pacific | Crushed/altered Eocene sediments at headlands | Highly deformed. |
| Lower Cowlitz Valley (Olequa–Winlock) | Lewis, Cowlitz | Type Tejon-equivalent fauna; ~8,000 ft | Best fossil control for upper Eocene marine. |
| Pacific / Wahkiakum / Grays Harbor | SW WA | Eocene basalts intimately associated with younger marine deposits | Nasel River, Willapa basin. |

### Eocene Lithology Summary

Sediments: medium-to-coarse grained sandstones (often arkosic, derived from granitic and granodiorite source); thinly bedded and massive shales; sandy/shaly transitional beds; carbonaceous shales and coal seams (bituminous to lignite); conglomerates at unit bases.

Volcanics: basaltic to basic-andesitic flows, flow breccias, pumice, ash, tuff. Interbedded thin clay seams (cm to ~1 ft) between flows often contain carbonaceous material indicating vegetated intervals.

Thickness: highly variable. ~8,000 ft (Green River), ~2,000 ft (Newcastle), ~14,000 ft (Carbon River, Pierce County), ≥21,000 ft (Tenino–Centralia).

## Oligocene

Predominantly marine. Total aggregate thickness ~15,000 ft. Distribution: south side of the Strait of Juan de Fuca, Puget Sound basin, southwestern Washington. The **Clallam formation** is named for the type area along the Strait. Recognized by faunal zones, lower → upper:

1. *Molopophorus lincolnensis* Zone
2. *Turritella porterensis* Zone
3. *Acila gettysburgensis* Zone

The Eocene–Oligocene contact is conventionally placed at the upper limit of the Eocene basalt; interbedded sediments within the basalt are assigned to the Eocene.

## Miocene

Lower Miocene marine sandstones and conglomerates (e.g., east of Clallam Bay). Upper Miocene exposures at Cape Elizabeth, Point Grenville, Quinault River, Grays Harbor, Quillayute. The **Snoqualmie granodiorite** is a Miocene intrusive on the western Cascade slope. The **Enumclaw volcanic series** caps parts of the western Cascade foothills; possible feeder dikes intrude older Eocene sediments in the Raging River area.

## Structural Framework

Western Washington Tertiary strata are arranged in broad NE-trending anticlines and synclines pitching south, cut by numerous faults. The **Newcastle–Issaquah anticline** is a key structural element extending from the Newcastle Hills southwest across Lake Washington toward Duwamish Station. The **Taylor syncline** in the Raging River area and the **Green River canyon** structures (parallel folds, average trend N20°E) are important for projecting unit subsurface positions.

## How to Use This Skill in Agent Reasoning

When an agent receives an AOI in western WA, follow this procedure:

1. Identify which Weaver "area" the AOI falls into (use county and the area table above).
2. Cite the area in the agent's `evidence` strings (e.g., `"AOI overlaps Weaver's Green River Eocene area; Bayne/Franklin/Kummer members expected."`).
3. Use the lithology summaries to set realistic expectations about host rock for the target mineral or commodity:
   - **Coal / lignite / petroleum source rock** → see the `wa-eocene-coal-fields` skill.
   - **Placer gold / heavy minerals** → arkosic Eocene sandstones derived from granodiorite source are candidate paleo-placer hosts; check Index granodiorite watershed in the `wa-pretertiary-basement` skill.
   - **Hydrothermal mineralization** → focus on the Snoqualmie granodiorite contact aureole and on dike swarms (Raging River area).
4. Add `data_sources_used: ["Weaver_1916_WGS_Bulletin_13"]` to the AgentResult so the citation is preserved.

## Cross-references to Other Skills

- `wa-pretertiary-basement` — Hoh formation, Index granodiorite, Old Metamorphic Series.
- `wa-eocene-coal-fields` — coal-bearing strata of King/Pierce/Lewis counties (also relevant as petroleum source rocks).
- `wa-historical-geology-source` — guidance on citing Weaver (1916) and reconciling its 1916 nomenclature with modern stratigraphic names.
