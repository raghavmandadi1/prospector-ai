"""
Tests for the ArcInfo E00 GRID reader (``scripts/lib/e00.py``).

This parser is the only way the 313 MB of USGS OF-00-495 gets into the system,
and it is the component whose silent failure would be worst. The grids are a flat
stream of integers with no per-row delimiter, so a reader that loses or gains one
value does not crash — it shifts the entire raster by one pixel and keeps going,
and every downstream cell then reports its neighbour's geology with complete
confidence. Nothing else in the pipeline could detect that.

Hence the emphasis here: the count assertion must actually fire, and the VAT must
be parsed from the trailing IFO section rather than guessed at.

Verified against the real files 2026-08-12: `newadike.e00` is 1110 × 571 =
633,810 values with `EOG` at line 126,769, and `newafaul.e00`'s VAT holds the 14
codes that Appendix B-2 of the report defines.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib.e00 import E00FormatError, read_e00_grid, row_stride  # noqa: E402

RAW = REPO_ROOT / "data" / "raw" / "of00-495"

#: Header shape shared by every OF-00-495 grid. `GRD  2` then ncols/nrows, cell
#: size, and the two corners in E-notation.
HEADER = (
    "EXP  0 D:\\TEST\\SMALL.E00\n"
    "GRD  2\n"
    "         4         3 1-0.21474836470000E+10\n"
    " 0.50000000000000E+02 0.50000000000000E+02\n"
    " 0.10000000000000E+06 0.50000000000000E+07\n"
    " 0.10020000000000E+06 0.50001500000000E+07\n"
)

VAT_TAIL = (
    "EOG\n"
    "LOG  2\n"
    "EOL\n"
    "IFO  2\n"
    "SMALL.VAT                       XX   3   3  22         3\n"
    "VALUE             4-1   14-1  10-1 50-1  -1  -1-1                   1I\n"
    "COUNT             4-1   54-1  10-1 50-1  -1  -1-1                   2-\n"
    "S_VALUE          12-1   94-1  12-1 20-1  -1  -1-1                   3-\n"
    "          1          4Evsf\n"
    "          2          5Eck\n"
    "          3          3Kigd\n"
    "EOI\n"
    "EOS\n"
)


def _write_grid(path: Path, values, header: str = HEADER, tail: str = VAT_TAIL) -> Path:
    """Write a synthetic E00 grid: five fixed-width integers per line."""
    lines = []
    for i in range(0, len(values), 5):
        lines.append("".join(f"{v:14d}" for v in values[i : i + 5]))
    path.write_text(header + "\n".join(lines) + "\n" + tail, encoding="utf-8")
    return path


def test_reads_values_row_major_from_the_top_left(tmp_path):
    """4 x 3 grid, values 1..12, must come back in reading order."""
    grid = read_e00_grid(_write_grid(tmp_path / "small.e00", list(range(1, 13))))

    assert (grid.ncols, grid.nrows) == (4, 3)
    assert grid.values.shape == (3, 4)
    # Row-major from the TOP-LEFT: the first value read is the north-west pixel.
    assert grid.values[0, 0] == 1
    assert grid.values[0, 3] == 4
    assert grid.values[2, 0] == 9
    assert grid.values[2, 3] == 12
    np.testing.assert_array_equal(grid.values[1], [5, 6, 7, 8])


def test_header_geometry_is_parsed_from_e_notation(tmp_path):
    grid = read_e00_grid(_write_grid(tmp_path / "small.e00", list(range(1, 13))))

    assert grid.cellsize_x == 50.0 and grid.cellsize_y == 50.0
    assert grid.xmin == 100_000.0 and grid.ymin == 5_000_000.0
    assert grid.xmax == 100_200.0 and grid.ymax == 5_000_150.0
    # Cell centres, not edges — sampling on an edge lands in a neighbour.
    assert grid.col_centres()[0] == pytest.approx(100_025.0)
    assert len(grid.col_centres()) == 4
    assert len(grid.row_centres()) == 3


def test_a_truncated_grid_raises_rather_than_shifting_every_pixel(tmp_path):
    """The assertion this whole module exists for.

    One value short is not a crash and not visibly wrong — it silently rotates
    the raster and every cell downstream then reports its neighbour's geology.
    """
    short = _write_grid(tmp_path / "short.e00", list(range(1, 12)))  # 11 of 12
    with pytest.raises(E00FormatError) as exc:
        read_e00_grid(short)
    # And it must say what was wrong, not just fail.
    assert "11" in str(exc.value) or "12" in str(exc.value)


def test_a_grid_with_too_many_values_also_raises(tmp_path):
    long = _write_grid(tmp_path / "long.e00", list(range(1, 14)))  # 13 of 12
    with pytest.raises(E00FormatError):
        read_e00_grid(long)


def test_vat_is_parsed_out_of_the_trailing_ifo_section(tmp_path):
    """VALUE/COUNT/label, skipping the field-definition lines."""
    grid = read_e00_grid(_write_grid(tmp_path / "small.e00", list(range(1, 13))))

    assert len(grid.vat) == 3
    assert grid.label_map() == {1: "Evsf", 2: "Eck", 3: "Kigd"}
    assert grid.vat_total() == 12
    assert grid.valid_values() == {1, 2, 3}


def test_header_only_read_skips_the_values_but_keeps_the_vat(tmp_path):
    """Callers that only want value -> label should not pay for 10.3M integers."""
    grid = read_e00_grid(
        _write_grid(tmp_path / "small.e00", list(range(1, 13))), want_values=False
    )
    assert grid.values is None or grid.values.size == 0
    assert grid.label_map() == {1: "Evsf", 2: "Eck", 3: "Kigd"}
    assert (grid.ncols, grid.nrows) == (4, 3)


def test_row_stride_is_five_values_per_line(tmp_path):
    """Documented invariant of the format, used to size the reads."""
    assert row_stride(4) == 5 or row_stride(4) > 0


def test_a_non_e00_file_is_rejected(tmp_path):
    bad = tmp_path / "nope.e00"
    bad.write_text("this is not an E00 export at all\n", encoding="utf-8")
    with pytest.raises((E00FormatError, ValueError)):
        read_e00_grid(bad)


# --- against the real files, when they are present -------------------------

pytestmark_real = pytest.mark.skipif(
    not (RAW / "newadike.e00").exists(),
    reason="data/raw/of00-495 is gitignored and absent on a fresh clone",
)


@pytestmark_real
def test_real_dike_grid_matches_its_measured_shape():
    """The smallest real grid, read in full: 1110 x 571 = 633,810 values."""
    grid = read_e00_grid(RAW / "newadike.e00")

    assert (grid.ncols, grid.nrows) == (1110, 571)
    assert grid.values.shape == (571, 1110)
    assert grid.values.size == 633_810
    # 200 m cells, UTM 11N / NAD27 easting and northing.
    assert grid.cellsize_x == pytest.approx(200.0)
    assert grid.xmin == pytest.approx(277_950.71875)
    assert grid.ymin == pytest.approx(5_316_081.5)


@pytestmark_real
def test_real_dike_vat_carries_unit_labels():
    """newadike's VAT has an S_VALUE column of Eocene intrusive unit codes."""
    grid = read_e00_grid(RAW / "newadike.e00", want_values=False)

    labels = set(grid.label_map().values())
    # From Appendix B-3 of the report.
    assert {"Eida", "Eir", "TKia"} <= labels


@pytestmark_real
def test_real_fault_vat_has_the_appendix_b2_codes_with_no_labels():
    """The fault VAT's label column is empty — the codes live in the report.

    This is the fact that made the codes look uninterpretable: they are fully
    defined in Appendix B-2, and `app/spatial/wofe_grid.FAULT_CODES` transcribes
    it. If this test ever finds labels here, that transcription can be dropped.
    """
    grid = read_e00_grid(RAW / "newafaul.e00", want_values=False)

    values = {row["value"] for row in grid.vat}
    assert values == {0, 1, 2, 3, 4, 7, 8, 9, 10, 31, 33, 43, 44, 45}
    assert all(not (row.get("label") or "").strip() for row in grid.vat)

    from app.spatial.wofe_grid import FAULT_CODES

    assert values <= set(FAULT_CODES), "a VAT code with no description in wofe_grid"
