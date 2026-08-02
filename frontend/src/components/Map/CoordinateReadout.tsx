import { cellIdForPoint, toDMS, toUTM } from './coords'

interface Props {
  lng: number
  lat: number
  /** Analysis resolution the cell id is computed at, metres. */
  resolutionM: number
}

/**
 * Cursor position in the three forms this project needs.
 *
 * Decimal degrees for copying into anything modern; DMS because that is how
 * coordinates appear in USGS reports and old literature; UTM because mining
 * ground and claim dimensions are described that way in the field.
 *
 * The fourth line is the cell id under the cursor. Run records and benchmark
 * reports store bare cell ids — no geometry — so without this, locating a
 * reported cell on the map means running a script. With it, the map is the
 * lookup table.
 */
export default function CoordinateReadout({ lng, lat, resolutionM }: Props) {
  const utm = toUTM(lng, lat)
  const cellId = cellIdForPoint(lng, lat, resolutionM)

  return (
    <div className="absolute bottom-3 left-3 z-10 bg-gray-900/85 text-gray-100 text-[11px] font-mono px-2.5 py-1.5 rounded border border-gray-700 pointer-events-none leading-relaxed">
      <div>
        {lat.toFixed(5)}, {lng.toFixed(5)}
      </div>
      <div>{toDMS(lat, lng)}</div>
      <div>
        UTM {utm.zone}
        {utm.band} {utm.easting.toLocaleString()} E{' '}
        {utm.northing.toLocaleString()} N
      </div>
      <div className="text-orange-300">{cellId}</div>
    </div>
  )
}
