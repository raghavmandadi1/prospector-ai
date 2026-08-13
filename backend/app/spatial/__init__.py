"""
Local, file-backed spatial context: the agents' evidence base.

Everything in this package reads files on disk — GeoJSON extracts, SQLite
stores, user field pins — and never PostGIS. That is deliberate: the PostGIS
spatial-context query is dead on the dev path (CLAUDE.md → Known Gaps #2), so
new capability wired behind it would be capability nobody can use. See
"steps for raghav 2.0" §31.

Nothing is imported eagerly here. A missing artifact must cost you an overlay
and a thinner prompt, never an ImportError at app start.

Modules:
    geometry      local equirectangular metre frame — the one definition of
                  "how far apart are these two points" in the codebase
    occurrences   WA DNR mines and their ASSAYS / PRODUCTION / LOCATION_ACCURACY
                  flags, mining districts, IAML workings
    geology       WA DNR 1:24k surface geology — units, faults, folds, dikes.
                  A 342-quadrangle mosaic with real coverage gaps; see the
                  module docstring before trusting its absence for anything.
    wofe_grid     USGS OF-00-495 NE Washington grids on the analysis ladder, and
                  the published OF01-501 contrasts they are keyed to
    user_sites    imported field pins, and the rule that a `truth` pin never
                  reaches a model
    local_store   assembles all of the above into one spatial_context dict

Built by ``scripts/build_reference_extracts.py``, ``build_geology_store.py``,
``build_of00495.py`` and ``import_field_pins.py`` from ``data/raw/``.
"""
