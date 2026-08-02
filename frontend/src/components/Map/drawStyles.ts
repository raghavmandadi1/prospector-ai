/**
 * Draw-control styles, corrected for MapLibre.
 *
 * `@mapbox/mapbox-gl-draw`'s default theme writes its dashed-line paint as
 *
 *     'line-dasharray': ['case', [...], [0.2, 2], [2, 0]]
 *
 * Mapbox GL accepts the bare arrays as literals; MapLibre's validator reads
 * them as expressions and refuses to add the layer. The result is that
 * `gl-draw-lines` never exists, so while you are drawing an AOI the polygon
 * edge is invisible until you close it — you place vertices blind.
 *
 * `['literal', […]]` is *not* the fix: MapLibre rejects data-driven expressions
 * for `line-dasharray` outright ("data expressions not supported"), whatever
 * they contain. The branch has to move out of the paint property and into a
 * layer filter, so the one upstream layer becomes two.
 *
 * Everything else is the upstream theme, restyled to sit on a topographic
 * basemap: the default cyan reads as water over USGS topo, so the inactive
 * colour is a warmer tone that does not.
 */
const ACTIVE = '#fbb03b' // orange
const IDLE = '#7c3aed'   // violet — distinct from hydrography and the score ramp
const WHITE = '#fff'

// mapbox-gl-draw's style objects are looser than MapLibre's LayerSpecification
// (they use the legacy `$type` filter form), so this is deliberately untyped.
export const DRAW_STYLES: object[] = [
  {
    id: 'gl-draw-polygon-fill',
    type: 'fill',
    filter: ['all', ['==', '$type', 'Polygon']],
    paint: {
      'fill-color': ['case', ['==', ['get', 'active'], 'true'], ACTIVE, IDLE],
      'fill-opacity': 0.08,
    },
  },
  // Two layers rather than one, because MapLibre does not support data-driven
  // expressions for `line-dasharray` *at all* — the branch has to move from the
  // paint property into a layer filter.
  {
    id: 'gl-draw-lines-active',
    type: 'line',
    filter: [
      'all',
      ['any', ['==', '$type', 'LineString'], ['==', '$type', 'Polygon']],
      ['==', 'active', 'true'],
    ],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ACTIVE,
      'line-dasharray': [0.2, 2],
      'line-width': 2.5,
    },
  },
  {
    id: 'gl-draw-lines-static',
    type: 'line',
    filter: [
      'all',
      ['any', ['==', '$type', 'LineString'], ['==', '$type', 'Polygon']],
      ['!=', 'active', 'true'],
    ],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': IDLE,
      'line-width': 2.5,
    },
  },
  {
    id: 'gl-draw-point-outer',
    type: 'circle',
    filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'feature']],
    paint: {
      'circle-radius': ['case', ['==', ['get', 'active'], 'true'], 7, 5],
      'circle-color': WHITE,
    },
  },
  {
    id: 'gl-draw-point-inner',
    type: 'circle',
    filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'feature']],
    paint: {
      'circle-radius': ['case', ['==', ['get', 'active'], 'true'], 5, 3],
      'circle-color': ['case', ['==', ['get', 'active'], 'true'], ACTIVE, IDLE],
    },
  },
  {
    id: 'gl-draw-vertex-outer',
    type: 'circle',
    filter: [
      'all',
      ['==', '$type', 'Point'],
      ['==', 'meta', 'vertex'],
      ['!=', 'mode', 'simple_select'],
    ],
    paint: {
      'circle-radius': ['case', ['==', ['get', 'active'], 'true'], 7, 5],
      'circle-color': WHITE,
    },
  },
  {
    id: 'gl-draw-vertex-inner',
    type: 'circle',
    filter: [
      'all',
      ['==', '$type', 'Point'],
      ['==', 'meta', 'vertex'],
      ['!=', 'mode', 'simple_select'],
    ],
    paint: {
      'circle-radius': ['case', ['==', ['get', 'active'], 'true'], 5, 3],
      'circle-color': ACTIVE,
    },
  },
  {
    id: 'gl-draw-midpoint',
    type: 'circle',
    filter: ['all', ['==', 'meta', 'midpoint']],
    paint: { 'circle-radius': 3, 'circle-color': ACTIVE },
  },
]
