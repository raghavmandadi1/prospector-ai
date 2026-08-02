/**
 * Coordinate conversions for the map readout.
 *
 * Two projections are implemented here by hand rather than pulled in with
 * proj4: EPSG:5070 (NAD83 / Conus Albers), because the cell id under the cursor
 * has to be computed with *exactly* the same maths as
 * `backend/app/scoring/grid.py`, and UTM, because the field/literature readout
 * needs it. Both are round-trip checked against pyproj in
 * `backend/tests/test_grid_frontend_parity.py` — if you change a constant here,
 * that test is what tells you the map and the backend have diverged.
 */

// --- EPSG:5070, NAD83 / Conus Albers (GRS80) ------------------------------
// Must match backend/app/scoring/grid.py exactly.

const A = 6378137.0 // GRS80 semi-major axis, metres
const E2 = 0.006694380022900787 // GRS80 first eccentricity squared
const E = Math.sqrt(E2)

const LAT_1 = (29.5 * Math.PI) / 180
const LAT_2 = (45.5 * Math.PI) / 180
const LAT_0 = (23.0 * Math.PI) / 180
const LON_0 = (-96.0 * Math.PI) / 180

/** Grid definition — mirrors GRID_TAG / GRID_ORIGIN_* / RESOLUTION_LADDER. */
export const GRID_TAG = 'wa5070'
export const GRID_ORIGIN_X = -2_240_000
export const GRID_ORIGIN_Y = 2_656_000
export const RESOLUTION_LADDER = [125, 250, 500, 1000, 2000, 4000, 8000]

function q(phi: number): number {
  const s = Math.sin(phi)
  return (
    (1 - E2) *
    (s / (1 - E2 * s * s) -
      (1 / (2 * E)) * Math.log((1 - E * s) / (1 + E * s)))
  )
}

function m(phi: number): number {
  const s = Math.sin(phi)
  return Math.cos(phi) / Math.sqrt(1 - E2 * s * s)
}

const M1 = m(LAT_1)
const M2 = m(LAT_2)
const Q1 = q(LAT_1)
const Q2 = q(LAT_2)
const N = (M1 * M1 - M2 * M2) / (Q2 - Q1)
const C = M1 * M1 + N * Q1
const RHO_0 = (A * Math.sqrt(C - N * q(LAT_0))) / N

/** WGS84 lon/lat (degrees) → EPSG:5070 easting/northing (metres). */
export function toAlbers5070(lon: number, lat: number): { x: number; y: number } {
  const phi = (lat * Math.PI) / 180
  const lam = (lon * Math.PI) / 180
  const theta = N * (lam - LON_0)
  const rho = (A * Math.sqrt(C - N * q(phi))) / N
  return { x: rho * Math.sin(theta), y: RHO_0 - rho * Math.cos(theta) }
}

export function snapToLadder(resolutionM: number): number {
  const r = Math.max(resolutionM, 1)
  return RESOLUTION_LADDER.reduce((best, step) =>
    Math.abs(Math.log(step / r)) < Math.abs(Math.log(best / r)) ? step : best
  )
}

/**
 * The canonical cell id containing a point — the same string the backend puts
 * in run records, benchmark reports and the cache.
 */
export function cellIdForPoint(lon: number, lat: number, resolutionM: number): string {
  const res = snapToLadder(resolutionM)
  const { x, y } = toAlbers5070(lon, lat)
  const col = Math.floor((x - GRID_ORIGIN_X) / res)
  const row = Math.floor((y - GRID_ORIGIN_Y) / res)
  const pad = (v: number) => String(v).padStart(6, '0')
  return `${GRID_TAG}-${res}m-${pad(col)}-${pad(row)}`
}

// --- UTM (WGS84) ----------------------------------------------------------

const UTM_A = 6378137.0
const UTM_E2 = 0.00669437999014 // WGS84
const K0 = 0.9996

const BANDS = 'CDEFGHJKLMNPQRSTUVWX'

export interface UTMPoint {
  zone: number
  band: string
  easting: number
  northing: number
}

export function toUTM(lon: number, lat: number): UTMPoint {
  const zone = Math.floor((lon + 180) / 6) + 1
  const band = BANDS[Math.floor((Math.min(Math.max(lat, -80), 83) + 80) / 8)] ?? 'Z'

  const phi = (lat * Math.PI) / 180
  const lam = (lon * Math.PI) / 180
  const lam0 = ((zone * 6 - 183) * Math.PI) / 180

  const ep2 = UTM_E2 / (1 - UTM_E2)
  const nu = UTM_A / Math.sqrt(1 - UTM_E2 * Math.sin(phi) ** 2)
  const T = Math.tan(phi) ** 2
  const Cc = ep2 * Math.cos(phi) ** 2
  const Aa = Math.cos(phi) * (lam - lam0)

  const e2 = UTM_E2
  const M =
    UTM_A *
    ((1 - e2 / 4 - (3 * e2 ** 2) / 64 - (5 * e2 ** 3) / 256) * phi -
      ((3 * e2) / 8 + (3 * e2 ** 2) / 32 + (45 * e2 ** 3) / 1024) * Math.sin(2 * phi) +
      ((15 * e2 ** 2) / 256 + (45 * e2 ** 3) / 1024) * Math.sin(4 * phi) -
      ((35 * e2 ** 3) / 3072) * Math.sin(6 * phi))

  const easting =
    K0 *
      nu *
      (Aa +
        ((1 - T + Cc) * Aa ** 3) / 6 +
        ((5 - 18 * T + T ** 2 + 72 * Cc - 58 * ep2) * Aa ** 5) / 120) +
    500000

  let northing =
    K0 *
    (M +
      nu *
        Math.tan(phi) *
        (Aa ** 2 / 2 +
          ((5 - T + 9 * Cc + 4 * Cc ** 2) * Aa ** 4) / 24 +
          ((61 - 58 * T + T ** 2 + 600 * Cc - 330 * ep2) * Aa ** 6) / 720))
  if (lat < 0) northing += 10000000

  return {
    zone,
    band,
    easting: Math.round(easting),
    northing: Math.round(northing),
  }
}

// --- Formatting and parsing ----------------------------------------------

function dms(value: number, hemis: [string, string]): string {
  const h = value >= 0 ? hemis[0] : hemis[1]
  const abs = Math.abs(value)
  const d = Math.floor(abs)
  const mFloat = (abs - d) * 60
  const mm = Math.floor(mFloat)
  const ss = ((mFloat - mm) * 60).toFixed(1)
  return `${d}°${String(mm).padStart(2, '0')}'${ss.padStart(4, '0')}"${h}`
}

export function toDMS(lat: number, lon: number): string {
  return `${dms(lat, ['N', 'S'])}  ${dms(lon, ['E', 'W'])}`
}

/**
 * Parse a pasted coordinate.
 *
 * Accepts decimal degrees, degrees + decimal minutes, and degrees/minutes/
 * seconds, with or without hemisphere letters — because coordinates get
 * transcribed out of USGS reports and old literature in all three, and having
 * to normalise them by hand before pasting defeats the point.
 */
export function parseCoordinate(input: string): { lng: number; lat: number } | null {
  const text = input.trim()
  if (!text) return null

  // Decimal degrees: "47.6512, -121.5488"
  const dd = text.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/)
  if (dd) {
    const a = parseFloat(dd[1])
    const b = parseFloat(dd[2])
    // Washington: latitude first is the overwhelmingly common ordering, but a
    // value beyond ±90 can only be a longitude, so accept either order.
    const [lat, lng] = Math.abs(a) > 90 ? [b, a] : [a, b]
    return Number.isFinite(lat) && Number.isFinite(lng) ? { lng, lat } : null
  }

  // DMS / DM with hemisphere letters
  const parts = text.match(
    /(\d+(?:\.\d+)?)\s*[°d:\s]\s*(?:(\d+(?:\.\d+)?)\s*['m:\s]\s*)?(?:(\d+(?:\.\d+)?)\s*["s]?\s*)?([NSEW])/gi
  )
  if (parts && parts.length >= 2) {
    const vals = parts.map((p) => {
      const m2 = p.match(
        /(\d+(?:\.\d+)?)\s*[°d:\s]\s*(?:(\d+(?:\.\d+)?)\s*['m:\s]\s*)?(?:(\d+(?:\.\d+)?)\s*["s]?\s*)?([NSEW])/i
      )!
      const deg = parseFloat(m2[1])
      const min = m2[2] ? parseFloat(m2[2]) : 0
      const sec = m2[3] ? parseFloat(m2[3]) : 0
      const h = m2[4].toUpperCase()
      const v = deg + min / 60 + sec / 3600
      return { v: h === 'S' || h === 'W' ? -v : v, h }
    })
    const lat = vals.find((x) => x.h === 'N' || x.h === 'S')
    const lng = vals.find((x) => x.h === 'E' || x.h === 'W')
    if (lat && lng) return { lng: lng.v, lat: lat.v }
  }

  return null
}
