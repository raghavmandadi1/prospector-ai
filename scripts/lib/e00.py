"""
Dependency-light reader for ArcInfo **GRID** exports (``.e00``), ASCII flavour.

Why this exists: ``data/raw/of00-495/*.e00`` (USGS OF-00-495, the only dataset
keyed to the published OF01-501 weights-of-evidence contrasts) is 325 MB of
ArcInfo export that nothing on this machine can read. There is no GDAL CLI, no
``osgeo``, no ``ogr2ogr`` and no Docker here, and ``scripts/convert_of00_495.sh``
needs all three. The format is simple enough that reading it directly is less
work than installing a toolchain — so this module does that, with numpy as its
only dependency.

Format, as verified against all four files in ``data/raw/of00-495/``::

    line 1  EXP  0 <original DOS path>
    line 2  GRD  2
    line 3  <ncols><nrows> ... <nodata as E-notation>   e.g. "      4476      2310 1-0.21474836470000E+10"
    line 4  <cellsize_x> <cellsize_y>                   E-notation
    line 5  <xmin> <ymin>                               E-notation, projection units
    line 6  <xmax> <ymax>
    line 7+ whitespace-separated ints, 5 per line, **row-major from the TOP-LEFT**,
            then a line starting "EOG"
    then    LOG ... EOL, then "IFO  2" holding the info tables:
              <NAME>.STA  — min/max/mean/stdv, ignored here
              <NAME>.VAT  — the value attribute table we want
            then EOI, EOS

**Each raster row is padded out to a whole number of 5-value output lines**, so
the data block holds ``nrows * ceil(ncols/5)*5`` values, not ``nrows * ncols``.
This is easy to miss: ``newadike`` has ncols=1110, a multiple of 5, so it needs
no padding and reads correctly under the naive assumption — while ``newafaul``
(ncols=2224) carries one extra value per row and ``newageol`` (ncols=4476)
carries four. The padding is *not* nodata; it repeats live values, so ignoring it
shifts every row and quietly mis-assigns every pixel. Proof that the pad sits at
the end of the row rather than the start: with the last ``stride-ncols`` columns
dropped, the per-value pixel counts match the VAT's own ``COUNT`` column exactly
for all four grids; dropping columns from the front does not. :func:`_read_values`
re-derives the stride from the parsed length and refuses to guess if it does not
divide evenly.

Line endings are CRLF. Coordinates are cell **corners**: for every one of the
four grids ``(xmax - xmin) / cellsize_x == ncols`` exactly, so pixel centres are
at ``xmin + (col + 0.5) * cellsize_x`` and — because rows run downward from the
top — ``ymax - (row + 0.5) * cellsize_y``. Getting that flip wrong silently
mirrors the whole raster, which is why :meth:`E00Grid.row_centres` exists rather
than leaving the arithmetic to each caller.

Nodata is ``-2147483647`` (encoded on line 3 as ``-0.21474836470000E+10``).
For the labelled grids, anything not present in the VAT is also treated as
nodata — see :meth:`E00Grid.valid_values`.

Not supported (and not needed): compressed E00, coverages (ARC/PAL/LAB
sections), tables other than ``.VAT``, or float grids.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import numpy as np

logger = logging.getLogger(__name__)

#: ArcInfo integer-grid nodata. Also written on header line 3 in E-notation.
E00_NODATA = -2147483647

#: Values per output line in the data block. Each raster row is padded out to a
#: whole number of these — see the module docstring.
VALUES_PER_LINE = 5

#: Bytes read per pass while parsing the data block. 8 MiB keeps peak memory
#: bounded on the 149 MB newageol.e00 while still giving numpy a big enough
#: buffer that its C text parser dominates the runtime.
_CHUNK_BYTES = 8 << 20

#: Bytes of file tail searched for the VAT before widening. The VAT is the last
#: thing in the file; 170 records is ~6 KB, so 256 KiB is generous.
_TAIL_BYTES = 256 << 10

#: A VAT data row: two fixed-width leading integers then an optional label that
#: may butt straight up against the count column (``        143      27048Zhmv``).
_VAT_ROW_RE = re.compile(rb"^\s*(-?\d+)\s+(-?\d+)(.*)$")

#: The VAT table header, e.g. ``NEWAGEOL.VAT   XX   3   3  22       170``.
#: The trailing integer is the record count; the first integer after ``XX`` is
#: the field count, used only as a cross-check.
_VAT_HEADER_RE = re.compile(rb"^(\S+)\.VAT\s")


class E00FormatError(ValueError):
    """Raised when a file is not the ASCII ArcInfo GRID export we expect.

    Always raised loudly rather than degraded: a half-understood raster produces
    plausible-looking cell values that are wrong, and every downstream cell id
    inherits the error.
    """


@dataclass
class E00Grid:
    """One ArcInfo integer grid, plus its value attribute table."""

    ncols: int
    nrows: int
    cellsize_x: float
    cellsize_y: float
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    #: ``(nrows, ncols)`` int32, row-major **from the top-left**.
    #: ``None`` when read with ``want_values=False``.
    values: Optional[np.ndarray]
    nodata: int = E00_NODATA
    #: One dict per VAT record: ``{"value": int, "count": int, "label": str}``.
    #: ``label`` is ``""`` for the code-only grids (newafaul, newafold).
    vat: List[Dict[str, Any]] = field(default_factory=list)
    #: Table name from the VAT header, e.g. ``NEWAGEOL`` or ``NE_WA_DIKE``.
    vat_name: str = ""
    #: Source file, for provenance strings.
    path: Optional[Path] = None
    #: Set when ``max_rows`` truncated the read (smoke tests only).
    truncated: bool = False

    # -- derived geometry ---------------------------------------------------

    def col_centres(self) -> np.ndarray:
        """X of every pixel-column centre, in the grid's own projection units."""
        return self.xmin + (np.arange(self.ncols, dtype=np.float64) + 0.5) * self.cellsize_x

    def row_centres(self) -> np.ndarray:
        """Y of every pixel-row centre. Row 0 is the **top** (northernmost) row."""
        return self.ymax - (np.arange(self.nrows, dtype=np.float64) + 0.5) * self.cellsize_y

    # -- VAT helpers -------------------------------------------------------

    def label_map(self) -> Dict[int, str]:
        """``value -> label`` for records that carry a label."""
        return {r["value"]: r["label"] for r in self.vat if r["label"]}

    def valid_values(self) -> Set[int]:
        """Values the VAT declares. Anything else in the raster is nodata.

        The VAT is authoritative: it is how ArcInfo recorded which codes the grid
        actually contains, and its per-value ``count`` lets a caller cross-check
        an aggregation against the source without re-reading 149 MB.
        """
        return {r["value"] for r in self.vat}

    def vat_total(self) -> int:
        """Sum of VAT counts — the number of non-nodata pixels ArcInfo recorded."""
        return int(sum(r["count"] for r in self.vat))


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _floats(line: bytes) -> List[float]:
    return [float(tok) for tok in line.split()]


def _read_header(fh) -> Dict[str, Any]:
    """Consume the six header lines. Leaves ``fh`` positioned at the data block."""
    raw = [fh.readline() for _ in range(6)]
    if not raw[0].startswith(b"EXP"):
        raise E00FormatError(f"Missing 'EXP' magic on line 1: {raw[0][:40]!r}")
    if not raw[1].startswith(b"GRD"):
        raise E00FormatError(
            f"Line 2 is {raw[1][:20]!r}, not 'GRD' — this reader only handles "
            "ArcInfo GRID exports, not coverages or TINs."
        )

    # Line 3 is fixed-width and runs the nodata float straight into the
    # preceding integer field ("... 571 1-0.21474836470000E+10"), so split the
    # leading two integers with a regex rather than on whitespace.
    m = re.match(rb"^\s*(\d+)\s+(\d+)\b", raw[2])
    if not m:
        raise E00FormatError(f"Cannot read ncols/nrows from line 3: {raw[2][:60]!r}")
    ncols, nrows = int(m.group(1)), int(m.group(2))

    nodata = E00_NODATA
    mn = re.search(rb"[-+]?\d*\.\d+[Ee][-+]?\d+", raw[2])
    if mn:
        nodata = int(round(float(mn.group(0))))

    cellsize = _floats(raw[3])
    lower_left = _floats(raw[4])
    upper_right = _floats(raw[5])
    if len(cellsize) < 2 or len(lower_left) < 2 or len(upper_right) < 2:
        raise E00FormatError("Header lines 4-6 do not each hold two numbers")

    hdr = {
        "ncols": ncols,
        "nrows": nrows,
        "cellsize_x": cellsize[0],
        "cellsize_y": cellsize[1],
        "xmin": lower_left[0],
        "ymin": lower_left[1],
        "xmax": upper_right[0],
        "ymax": upper_right[1],
        "nodata": nodata,
    }

    # Consistency of extent vs cell count is a cheap way to catch a
    # misidentified header. All four OF-00-495 grids match exactly; warn rather
    # than raise so an oddly-rounded grid is still readable.
    for axis, n, size, lo, hi in (
        ("x", ncols, hdr["cellsize_x"], hdr["xmin"], hdr["xmax"]),
        ("y", nrows, hdr["cellsize_y"], hdr["ymin"], hdr["ymax"]),
    ):
        implied = (hi - lo) / size
        if abs(implied - n) > 0.01:
            logger.warning(
                "%s extent implies %.3f cells but header says %d — "
                "corner/centre convention may differ",
                axis,
                implied,
                n,
            )
    return hdr


# ---------------------------------------------------------------------------
# Data block
# ---------------------------------------------------------------------------


def _iter_value_chunks(fh, want: Optional[int] = None) -> Iterator[np.ndarray]:
    """Yield int32 arrays from the data block, stopping at ``EOG``.

    ``want`` is an early-exit bound honoured only approximately (to the nearest
    chunk) and is used solely by ``max_rows``. A full read passes ``None`` so the
    total is whatever the file actually holds — which is what makes the caller's
    length check in :func:`_read_values` worth anything.
    """
    buf = b""
    seen = 0
    while True:
        chunk = fh.read(_CHUNK_BYTES)
        data = buf + chunk
        # "EOG" can straddle a chunk boundary, which is why the search happens
        # on buf+chunk rather than on chunk alone. No numeric token can contain
        # a letter, so a bare find() cannot produce a false positive.
        end = data.find(b"EOG")
        if end >= 0:
            rest, buf, last = data[:end], b"", True
        elif not chunk:
            rest, buf, last = data, b"", True
        else:
            # Keep the trailing (possibly incomplete) token for the next pass.
            cut = max(data.rfind(b" "), data.rfind(b"\n"), data.rfind(b"\r"))
            if cut < 0:
                buf = data
                continue
            rest, buf, last = data[:cut], data[cut:], False

        # np.fromstring(b"   ", sep=" ") returns a phantom [0]; skip blanks.
        if rest.strip():
            arr = np.fromstring(rest, dtype=np.int32, sep=" ")
            if arr.size:
                seen += arr.size
                yield arr
        if last or (want is not None and seen >= want):
            return


def row_stride(ncols: int) -> int:
    """Values stored per raster row, including the end-of-row line padding."""
    return math.ceil(ncols / VALUES_PER_LINE) * VALUES_PER_LINE


def _read_values(fh, hdr: Dict[str, Any], max_rows: Optional[int]) -> np.ndarray:
    nrows = hdr["nrows"] if max_rows is None else min(max_rows, hdr["nrows"])
    ncols = hdr["ncols"]
    stride = row_stride(ncols)
    want = nrows * stride

    parts = list(_iter_value_chunks(fh, want if max_rows is not None else None))
    flat = np.concatenate(parts) if parts else np.empty(0, dtype=np.int32)

    if max_rows is None:
        # Load-bearing check. A silent off-by-one here does not crash; it shifts
        # every subsequent row and quietly mis-assigns every derived cell, which
        # is far worse than a failed build. If the padded stride does not fit,
        # re-derive it from the data and only accept a stride that is a clean
        # divisor within one output line of ncols.
        if flat.size != want:
            inferred = flat.size // nrows if nrows else 0
            if flat.size and nrows and flat.size % nrows == 0 and (
                ncols <= inferred < ncols + VALUES_PER_LINE
            ):
                logger.warning(
                    "Data block implies a row stride of %d, not the expected %d "
                    "(ncols=%d). Using the inferred stride.",
                    inferred,
                    stride,
                    ncols,
                )
                stride = inferred
            else:
                raise E00FormatError(
                    f"Expected {want} values ({ncols} cols padded to a stride of "
                    f"{stride} x {nrows} rows) but parsed {flat.size}. The data "
                    f"block is not the size the header implies; refusing to "
                    f"reshape and guess."
                )
    else:
        if flat.size < want:
            raise E00FormatError(
                f"Requested the first {nrows} rows ({want} values at stride "
                f"{stride}) but the data block only yielded {flat.size}"
            )
        flat = flat[:want]

    # Drop the end-of-row padding. `.copy()` because everything downstream keeps
    # this array around and a non-contiguous view of a 10 M-element buffer would
    # pin the padded original for the life of the build.
    grid = flat.reshape(nrows, stride)
    return grid if stride == ncols else grid[:, :ncols].copy()


# ---------------------------------------------------------------------------
# VAT (value attribute table), in the trailing IFO section
# ---------------------------------------------------------------------------


def _find_vat_text(path: Path) -> bytes:
    """Return the file tail from the VAT header onward, widening the read as needed."""
    size = path.stat().st_size
    window = _TAIL_BYTES
    while True:
        with path.open("rb") as fh:
            fh.seek(max(0, size - window))
            tail = fh.read()
        # Take the LAST VAT header in case the IFO section holds more than one.
        idx = tail.rfind(b".VAT")
        if idx >= 0:
            start = tail.rfind(b"\n", 0, idx) + 1
            return tail[start:]
        if window >= size:
            return b""
        window = min(window * 8, size)


def _parse_vat(path: Path) -> tuple[str, List[Dict[str, Any]]]:
    text = _find_vat_text(path)
    if not text:
        logger.warning("No .VAT table found in %s — values will be unlabelled", path.name)
        return "", []

    lines = text.replace(b"\r\n", b"\n").split(b"\n")
    m = _VAT_HEADER_RE.match(lines[0])
    if not m:
        logger.warning("VAT header did not parse: %r", lines[0][:80])
        return "", []
    name = m.group(1).decode("latin-1")

    ints = [int(t) for t in lines[0].split()[1:] if re.fullmatch(rb"-?\d+", t)]
    n_records = ints[-1] if ints else 0
    n_fields = ints[0] if ints else 0

    # Skip the field-definition lines. They start with a field name rather than
    # a number, so "first line that parses as a data row" is a more robust cut
    # than trusting the declared field count — which is only cross-checked.
    body: List[bytes] = []
    skipped = 0
    for line in lines[1:]:
        if not body and not _VAT_ROW_RE.match(line):
            skipped += 1
            continue
        if not line.strip() or line.startswith(b"EOI"):
            break
        body.append(line)
    if n_fields and skipped != n_fields:
        logger.warning(
            "%s.VAT declares %d fields but %d definition lines were skipped",
            name,
            n_fields,
            skipped,
        )

    rows: List[Dict[str, Any]] = []
    for line in body[:n_records] if n_records else body:
        m = _VAT_ROW_RE.match(line)
        if not m:
            logger.warning("Skipping unparseable %s.VAT row: %r", name, line[:60])
            continue
        rows.append(
            {
                "value": int(m.group(1)),
                "count": int(m.group(2)),
                "label": m.group(3).decode("latin-1").strip(),
            }
        )

    if n_records and len(rows) != n_records:
        raise E00FormatError(
            f"{name}.VAT declares {n_records} records but {len(rows)} parsed from "
            f"{path.name}. The value->label mapping would be incomplete."
        )
    return name, rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def read_e00_grid(
    path: str | Path,
    *,
    want_values: bool = True,
    max_rows: Optional[int] = None,
) -> E00Grid:
    """Read an ASCII ArcInfo GRID export.

    Args:
        path: the ``.e00`` file.
        want_values: ``False`` skips the data block entirely and returns header +
            VAT only. That is the common case for callers that just want the
            ``value -> label`` mapping, and it turns a 149 MB parse into two
            small seeks.
        max_rows: read only the first *N* raster rows (which are the
            **northernmost** rows). For smoke tests. ``ymin`` is adjusted so the
            returned extent still describes the data actually loaded.

    Raises:
        E00FormatError: on a non-GRID export, an unreadable header, a VAT record
            count mismatch, or a data block whose length disagrees with the
            header. None of these are recoverable without guessing.
    """
    path = Path(path)
    with path.open("rb") as fh:
        hdr = _read_header(fh)
        values = _read_values(fh, hdr, max_rows) if want_values else None

    vat_name, vat = _parse_vat(path)

    nrows = hdr["nrows"]
    ymin = hdr["ymin"]
    truncated = False
    if want_values and max_rows is not None and max_rows < nrows:
        nrows = max_rows
        # Rows run downward from ymax, so a truncated read keeps the top strip.
        ymin = hdr["ymax"] - nrows * hdr["cellsize_y"]
        truncated = True

    return E00Grid(
        ncols=hdr["ncols"],
        nrows=nrows,
        cellsize_x=hdr["cellsize_x"],
        cellsize_y=hdr["cellsize_y"],
        xmin=hdr["xmin"],
        ymin=ymin,
        xmax=hdr["xmax"],
        ymax=hdr["ymax"],
        values=values,
        nodata=hdr["nodata"],
        vat=vat,
        vat_name=vat_name,
        path=path,
        truncated=truncated,
    )
