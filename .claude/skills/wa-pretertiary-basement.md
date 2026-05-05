---
name: wa-pretertiary-basement
description: Reference for the pre-Tertiary basement units of western Washington — the Old Metamorphic Series, the Index granodiorite, and the Hoh formation — as documented by Weaver (1916, WA Geological Survey Bulletin 13, Chapter II). This skill should be used by lithology, structure, and proximity agents when the AOI lies along the western slope of the northern Cascades, in the Olympic Mountains, or along the western Olympic Peninsula coast, and the analysis needs to characterize basement-controlled mineralization, hydrothermal context, or oil-seep host rocks.
source: Weaver, C.E. (1916). The Tertiary Formations of Western Washington. Washington Geological Survey Bulletin No. 13, Chapter II.
---

# Western Washington Pre-Tertiary Basement

The pre-Tertiary basement of western Washington exposes three principal units below the Eocene Tejon-equivalent strata. Each has distinct prospecting implications.

## 1. Old Metamorphic Series

**Distribution:** Western slope of the northern Cascades and the central Olympic Mountains.

**Character:** The oldest crystalline basement exposed in the region; quartzites, slates, schists, and gneisses. Weaver suggests the central Olympic quartzites and slates may belong stratigraphically to the Hoh formation that has undergone differential metamorphism, though this correlation is uncertain.

**Prospecting relevance:**
- Host for orogenic / lode-style mineralization in the northern Cascade margin.
- Potential basement source for detrital heavy minerals in overlying Eocene arkosic sandstones.

## 2. Index Granodiorite

**Distribution:** Western slope of the northern Cascades, type locality at Index Mountain (Skykomish River drainage).

**Character:** Medium-to-coarse grained granodiorite intrusive into the Old Metamorphic Series. Likely Cretaceous–Paleogene plutonic basement.

**Prospecting relevance:**
- Granodiorite intrusives are a classic host for porphyry-style Cu / Mo / Au and contact-metasomatic skarn mineralization.
- Detrital source for arkosic sandstones in the Eocene coal-measure basins (Newcastle, Green River, Cedar Mountain) — relevant when assessing paleo-placer potential.
- Watershed downstream of Index Mountain (Skykomish River) is a candidate for placer gold from eroded plutonic source rocks.

## 3. Hoh Formation

**Distribution:** Western and southwestern slope of the Olympic Peninsula (Hoh River, Quillayute River, Queets River, Quinault River, Ozette Lake districts). Possibly equivalent to the metamorphosed core of the central Olympics.

**Character:** Predominantly sedimentary with local metamorphism. Lithologies:
- Medium-to-coarse grained, gritty, firmly consolidated sandstones (grayish brown), often containing small angular black slate fragments.
- Massive sandstone units 600–700 ft thick, locally grading laterally into conglomerate.
- A characteristic facies of **bluish-gray, coarse-grained, gritty muscovite-bearing sandstone** — sometimes muscovite-rich enough to give a banded/scaly appearance. **This is the key petroleum-indicator lithology.**
- Sandy shales and shales (dark brown).

**Prospecting relevance — petroleum:**
> "It is generally in this type of sandstones that indications of petroleum are found." (Weaver 1916, p. 71)

The muscovitic Hoh sandstones host the only direct petroleum shows Weaver describes in the bulletin's first three chapters. For any AOI on the western Olympic Peninsula targeting hydrocarbons, Hoh outcrop area should be flagged as a positive direct-evidence indicator. The detailed petroleum chapter (Chapter VIII, book pages 273–298) was *not included* in the uploaded PDF — a warning should be raised whenever this skill is used for petroleum targeting, recommending supplementary modern USGS data.

**Prospecting relevance — minerals:**
- Local low-grade metamorphism in the central Olympics could host minor metamorphic mineralization, but the unit is not known for significant metallic deposits.

## How to Use in Agent Reasoning

1. **AOI on western Olympic Peninsula coast (Hoh, Quillayute, Queets, Quinault, Ozette districts):**
   - Lithology agent: identify Hoh formation host rock; flag muscovitic gritty sandstone facies if petroleum is the target.
   - Add `warnings: ["Weaver 1916 Chapter VIII (petroleum) not in source PDF — supplement with modern data"]` for hydrocarbon analyses.
2. **AOI on western slope of the northern Cascades:**
   - Identify Index granodiorite contacts and Old Metamorphic Series outcrops.
   - For Cu/Mo/Au porphyry, skarn, or orogenic-Au targets, raise scoring confidence near Index granodiorite contact aureoles.
3. **Cross-reference with `wa-tertiary-stratigraphy`** to determine whether the AOI sits on basement or on Tertiary cover, and how much overburden masks the basement.
4. Cite `data_sources_used: ["Weaver_1916_WGS_Bulletin_13_Ch_II"]` in the AgentResult.
