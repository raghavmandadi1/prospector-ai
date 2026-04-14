"""
Default agent weight presets by target mineral.

Weights reflect the relative importance of each geological criterion
for different deposit types. These are starting points — users can
override per-run via the analysis job config.

Weight scale is relative (not required to sum to 1.0); the scoring
engine normalizes during weighted mean computation.

Reference: Mineral system analysis frameworks (Knox-Robinson & Wyborn 1997,
Porwal & Carranza 2015)
"""

DEFAULT_WEIGHTS: dict = {
    "gold": {
        # Structural controls dominate orogenic gold
        "structure": 0.30,
        # Lithology (reactive carbonate host vs unfavorable granite)
        "lithology": 0.25,
        # Geochemical pathfinder elements (As, Sb, Hg halos)
        "geochemistry": 0.20,
        # Historic production is strong validator
        "historical": 0.15,
        # Remote sensing alteration mapping
        "remote_sensing": 0.07,
        # Proximity to known gold deposits
        "proximity": 0.03,
    },
    "copper": {
        # Porphyry Cu: lithology (calc-alkaline intrusions) is key
        "lithology": 0.30,
        # Structural position relative to intrusive centers
        "structure": 0.20,
        # Cu-Mo-Re geochemical halos
        "geochemistry": 0.25,
        # Alteration zones (phyllic, potassic) from SWIR
        "remote_sensing": 0.15,
        # Proximity to known porphyry districts
        "proximity": 0.07,
        "historical": 0.03,
    },
    "silver": {
        "lithology": 0.25,
        "structure": 0.25,
        "geochemistry": 0.20,
        "historical": 0.15,
        "proximity": 0.10,
        "remote_sensing": 0.05,
    },
    "uranium": {
        # Roll-front: sedimentary lithology critical
        "lithology": 0.35,
        # Redox boundaries and structural traps
        "structure": 0.20,
        "geochemistry": 0.25,
        "historical": 0.10,
        "proximity": 0.07,
        "remote_sensing": 0.03,
    },
    "lithium": {
        # Li pegmatites: intrusive lithology dominant
        "lithology": 0.35,
        "geochemistry": 0.25,
        "structure": 0.15,
        "proximity": 0.15,
        "remote_sensing": 0.07,
        "historical": 0.03,
    },
}

# Fallback equal-weight preset for minerals not explicitly listed
EQUAL_WEIGHTS: dict = {
    "lithology": 1.0,
    "structure": 1.0,
    "geochemistry": 1.0,
    "historical": 1.0,
    "proximity": 1.0,
    "remote_sensing": 1.0,
}
