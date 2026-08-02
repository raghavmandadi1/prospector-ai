#!/usr/bin/env python3
"""
Triage and extract text from a directory of scanned geological reports.

This is the tool behind the per-source analyses committed under
`docs/intake_analyses/`. It answers two questions for each PDF in a directory:

  1. Is the text machine-extractable, or is it a scanned image needing OCR?
  2. If extractable, what does the full text say?

It writes a `_manifest.md` triage table plus one `<slug>.txt` full-text dump per
extractable PDF. The curated markdown in `docs/intake_analyses/` is written by
hand (or by an agent) on top of those dumps — this script does not generate it.

Source PDFs are gitignored; see "Source literature archive" in data/README.md
for what the archive contains and where to unzip it.

Usage
-----
    pip install pypdf pdfplumber

    # Triage only — fast, reads the first few pages of each PDF
    python scripts/extract_pdfs.py data/literature/I90Hiker

    # Triage + full text dumps
    python scripts/extract_pdfs.py data/literature/I90Hiker \
        --output-dir docs/intake_analyses/_raw --full-text

    # Limit pages per document (useful for very large map sheets)
    python scripts/extract_pdfs.py data/literature --recursive --max-pages 50
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from pypdf import PdfReader
    import pdfplumber
except ImportError:  # pragma: no cover - dependency guidance
    sys.exit(
        "Missing dependencies. Install them with:\n"
        "    pip install pypdf pdfplumber"
    )


# A PDF whose first `SAMPLE_PAGES` pages yield fewer than this many characters
# is almost certainly a scan with no text layer.
TEXT_LAYER_THRESHOLD = 100
SAMPLE_PAGES = 5


@dataclass
class Report:
    """Triage result for a single PDF."""

    path: Path
    pages: int = 0
    sample_chars: int = 0
    excerpt: str = ""
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.sample_chars >= TEXT_LAYER_THRESHOLD:
            return "text-extractable"
        return "scanned-needs-ocr"

    @property
    def slug(self) -> str:
        stem = re.sub(r"\.pdf$", "", self.path.stem, flags=re.I)
        stem = re.sub(r"[^\w\s-]", "", stem).strip().lower()
        return re.sub(r"[\s_-]+", "-", stem)[:80] or "untitled"


def triage(path: Path) -> Report:
    """Read page count and sample the first few pages for a text layer."""
    report = Report(path=path)
    try:
        report.pages = len(PdfReader(path).pages)
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:SAMPLE_PAGES]:
                text = page.extract_text()
                if not text:
                    continue
                report.sample_chars += len(text)
                if not report.excerpt:
                    report.excerpt = " ".join(text[:400].split())
    except Exception as exc:  # noqa: BLE001 - triage must never abort the batch
        report.error = str(exc)
    return report


def extract_full_text(path: Path, max_pages: int | None = None) -> str:
    """Extract the full text layer, one `[PAGE n]` marker per page."""
    chunks: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                if max_pages is not None and i >= max_pages:
                    chunks.append(f"\n[TRUNCATED after {max_pages} pages]\n")
                    break
                text = page.extract_text()
                if text:
                    chunks.append(f"\n[PAGE {i + 1}]\n{text}\n")
    except Exception as exc:  # noqa: BLE001
        return f"[ERROR: {exc}]"
    return "".join(chunks)


def write_manifest(reports: list[Report], output_dir: Path, input_dir: Path) -> Path:
    """Write the triage table that tells you which PDFs need OCR."""
    lines = [
        f"# PDF triage — `{input_dir}`",
        "",
        f"{len(reports)} document(s). "
        "`scanned-needs-ocr` entries have no text layer and must be OCR'd or "
        "read manually before their content can be cited.",
        "",
        "| Document | Pages | Status | Sample chars |",
        "|---|---:|---|---:|",
    ]
    for r in sorted(reports, key=lambda r: r.path.name):
        lines.append(
            f"| `{r.path.name}` | {r.pages} | {r.status} | {r.sample_chars} |"
        )

    lines += ["", "## Excerpts", ""]
    for r in sorted(reports, key=lambda r: r.path.name):
        lines.append(f"### {r.path.name}")
        if r.error:
            lines += [f"- **Error:** {r.error}", ""]
            continue
        lines += [
            f"- **Pages:** {r.pages}",
            f"- **Status:** {r.status}",
            f"- **First 400 chars:** {r.excerpt or '_(no text layer)_'}",
            "",
        ]

    manifest = output_dir / "_manifest.md"
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Triage and extract text from a directory of PDFs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing PDFs (e.g. data/literature/I90Hiker)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write _manifest.md and text dumps "
        "(default: <input_dir>/_extracted)",
    )
    parser.add_argument(
        "--full-text",
        action="store_true",
        help="Also dump full text for every text-extractable PDF",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Cap pages read per document during full-text extraction",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recurse into subdirectories",
    )
    args = parser.parse_args()

    input_dir: Path = args.input_dir
    if not input_dir.is_dir():
        parser.error(
            f"{input_dir} is not a directory. Source PDFs are gitignored — see "
            f"'Source literature archive' in data/README.md for where to get them."
        )

    pattern = "**/*.pdf" if args.recursive else "*.pdf"
    pdfs = sorted(p for p in input_dir.glob(pattern) if p.is_file())
    if not pdfs:
        parser.error(f"No PDFs found in {input_dir}")

    output_dir: Path = args.output_dir or (input_dir / "_extracted")
    output_dir.mkdir(parents=True, exist_ok=True)

    reports: list[Report] = []
    for path in pdfs:
        print(f"Triaging {path.name} ...", flush=True)
        report = triage(path)
        reports.append(report)
        print(f"  {report.pages} pages · {report.status}")

        if args.full_text and report.status == "text-extractable":
            text = extract_full_text(path, args.max_pages)
            dump = output_dir / f"{report.slug}.txt"
            dump.write_text(text, encoding="utf-8")
            print(f"  → {dump} ({len(text):,} chars)")

    manifest = write_manifest(reports, output_dir, input_dir)

    extractable = sum(1 for r in reports if r.status == "text-extractable")
    scanned = sum(1 for r in reports if r.status == "scanned-needs-ocr")
    errored = sum(1 for r in reports if r.status == "error")
    print(
        f"\n{len(reports)} document(s): {extractable} text-extractable, "
        f"{scanned} need OCR, {errored} errored."
        f"\nManifest: {manifest}"
    )
    return 1 if errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
