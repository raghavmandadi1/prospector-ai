---
name: wa-eocene-coal-fields
description: Reference for the Eocene coal-bearing strata of western Washington (Newcastle, Renton, Green River, Cedar Mountain, Raging River, and the Centralia–Tenino area), as documented by Weaver (1916, WA Geological Survey Bulletin 13). This skill should be used by lithology, historical, and proximity agents when the AOI lies in King, Pierce, Lewis, Cowlitz, or Thurston counties and the target commodity is coal, lignite, hydrocarbons, or any commodity whose host or source rock is Eocene coal-measure sediments. The Eocene coal measures are also the principal candidate petroleum source rocks of western Washington.
source: Weaver, C.E. (1916). The Tertiary Formations of Western Washington. Washington Geological Survey Bulletin No. 13, Chapter III.
---

# Western Washington Eocene Coal Fields

The Upper Eocene of western Washington (Tejon-equivalent) contains the most economically significant coal seams in the Pacific Northwest. These same coal-bearing intervals are the principal candidate source rocks for petroleum reported in southwestern Washington and the western Olympic Peninsula. This skill provides the field localities, type sections, and depositional context Weaver established in 1916.

## Depositional Setting

The Eocene coal measures formed in an estuarine basin in a state of differential oscillation. Marine, estuarine, and lacustrine beds are interbedded; coal seams formed during stable shallow-water / coastal-swamp intervals between subsidence pulses. The basin received clastic input from a granitic/granodioritic source (arkosic sandstones) interbedded with contemporaneous basalt and andesite flows.

This setting matters: coal seams are **lenticular** along strike, change rapidly in thickness and rank, and are interrupted by syndepositional faults and later igneous intrusion.

## Coal Field Inventory

### Newcastle Hills – Issaquah (King County)
- Anticlinal structure trending N45–70°W, dips 40–70° NE (Coal Creek headwaters).
- Sediments rest on Eocene basalt/andesite; ~2,000 ft sediment thickness.
- Productive seams along Coal Creek and at Issaquah; classic Pacific Northwest coal district of the early 20th century.
- Map reference: Plate IV, Map B (extends from Kitsap County across Seattle to the Cascade foothills).

### Green River (King County)
- Type section ~8,000 ft of Eocene sediments.
- Subdivided into three lithologic **members**:
  - **Bayne member** (lowest) — ~3,000+ ft, predominantly shales with subordinate sandstone and carbonaceous beds.
  - **Franklin member** — sandstones, banded shales, and the productive coal interval. Type locality at Franklin wagon bridge.
  - **Kummer member** (uppermost) — ~1,800 ft of light-colored coarse sandstones with intercalated shale and carbonaceous beds; basal massive sandstone ~475 ft.
- Coal seams range from a few inches to **50 ft thick** (lower-grade seams).
- Coal rank: bituminous to low-grade lignite, with strong vertical and lateral variation.
- Folded into parallel anticlines/synclines pitching south, average trend N20°E. Cut by an E–W trending fault through Sec. 19–21, T21N, R7E that dislocates the Franklin sandstone and is mapped in the underground workings of the Franklin mine.

### Cedar Mountain (King County)
- Lithologically similar to Renton; ~2,000 ft total section.
- Lower 800 ft contains the coal seams.

### Raging River – Taylor – Kerriston (King County)
- Eocene sediments confined to T23N R7E and northern T22N R7E.
- Low-grade coal and carbonaceous bands intercalated with sandstones and shales.
- **Heavily intruded by andesite and rhyolite dikes** (likely feeders to the late Miocene Enumclaw volcanic series). Coal seams are disrupted, faulted, and devolatilized near intrusions — important caveat for resource estimates here.
- Taylor mine crosscut tunnel exposes the best stratigraphic section.

### Renton (King County)
- Coal-bearing strata equivalent to Newcastle. Marine beds at Duwamish Station contain marine invertebrates interbedded with the lignite-bearing strata, confirming Upper Eocene (Tejon) age and brackish/coastal setting.

### Tenino – Centralia (Thurston / Lewis counties)
- Interbedded sediments and lavas dipping south at low angles. Estimated thickness ≥21,000 ft; coal-bearing intervals extensive but less folded than King County fields.

### Pierce County (Carbon River and southward)
- Eocene strata up to 14,000 ft thick. Base of formation unknown. Coal seams present; estuarine setting documented by interbedded marine fossils.

## Lithology — Field Identification Cues

- Coarse arkosic sandstones (granitic/granodioritic detritus, often cross-bedded).
- Medium-to-dark gray to chocolate-brown shales, often conchoidally fracturing.
- Carbonaceous shales grading to impure shaly lignite to coal.
- Volcanic interbeds: basalt/andesite flows, flow breccias, pumice-bearing tuffs; thin clay seams (often carbonaceous) between flows.
- Reworked volcanic tuff in some sandstone units (microscope confirms).

## Implications for Petroleum Prospecting

Weaver (1916) explicitly identifies the **Hoh formation** muscovite-bearing gritty bluish-gray sandstones (western Olympic Peninsula) as the units showing oil seeps and "indications of petroleum." The **Eocene coal-measure shales** of King–Pierce–Lewis–Cowlitz counties are the principal candidate source rocks for the SW Washington and Olympic Peninsula petroleum shows discussed in Chapter VIII (note: Chapter VIII is *not* included in the uploaded PDF — only its TOC entry).

When the AOI is in southwestern WA or the western Olympic Peninsula and the target is hydrocarbons:
- Flag the presence/absence of mature Eocene coal-measure shales as the source-rock proxy.
- Flag Hoh formation outcrops as direct petroleum-indicator horizons.
- Cite the absence of Chapter VIII data and recommend supplementary modern sources.

## How to Use in Agent Reasoning

1. If AOI overlaps any of the named coal fields above, the lithology_agent should:
   - Add evidence: `"AOI within Weaver (1916) <field name> Eocene coal field; coal-measure host rocks expected."`
   - Set higher confidence for coal-host commodities; flag dike intrusion (Raging River) as a negative for coal continuity.
2. If the target mineral is **placer gold**, note that the Newcastle/Green River arkosic sandstones contain detritus from the Index granodiorite and similar plutons — a possible (though weak) paleo-placer concentration mechanism.
3. For **petroleum** targets in SW WA / Olympic Peninsula, treat Eocene coal-measure shales as the source-rock indicator. Add a `warnings` entry that Chapter VIII of the source bulletin was not provided and recommend supplementing with USGS petroleum studies.
4. Always include `data_sources_used: ["Weaver_1916_WGS_Bulletin_13_Ch_III"]` in the AgentResult.
