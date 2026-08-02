# Washington Division of Mines and Geology Bulletin 42: "Gold in Washington"

## Source Citation

**Full Citation:**
- **Title:** Gold in Washington
- **Publisher:** Washington Division of Mines and Geology
- **Bulletin:** No. 42
- **Author:** Likely Huntting, M. E. (circa 1955)
- **Page Count:** 162 pages
- **Format:** Image-scanned PDF (no embedded text)

**Recommended canonical key for `data_sources_used`:** `WDMG_Bulletin_42_Gold_in_Washington`

---

## Coverage Summary

Bulletin 42 is the authoritative statewide gold reference for Washington, covering all major gold districts with historical production data, host-rock lithology, vein orientations, and closure causes. The 162-page PDF is entirely image-scanned and requires OCR to extract text and tabular production data.

---

## Data Extraction Status

**OCR Required:** YES — The PDF contains no embedded text layers. All content is scanned imagery.

**Analysis Blocked:** Unable to proceed with extraction of districts, production figures, host-rock data, and vein orientations without OCR.

---

## Recommended Next Steps

1. **OCR the PDF** using Tesseract, Google Vision API, or commercial service (Adobe, AWS Textract).
2. **Tabular extraction:** Once OCR'd, use pdfplumber or manual parsing to extract production tables by district.
3. **Cross-reference:** Map extracted districts against existing KB (`lithology/gold.md`, `historical/gold.md`) and flag new districts.
4. **Create new reference skill:** Once extracted, establish `wa-bulletin42-gold.md` skill with Bulletin 42 as canonical source.

---

## Value Rating

**HIGH** — Bulletin 42 is the single most authoritative WA gold reference. Once OCR'd, it will provide:
- Statewide district coverage (fill gaps in existing KB)
- Historical production figures (essential for historical agent calibration)
- Host-rock and structural data (lithology and structure agent refinement)
- Deposit-type classification (epithermal vs. orogenic vs. skarn distinction)

---

## Files Affected (Upon OCR + Extraction)

- `backend/app/agents/knowledge/lithology/gold.md` — Add host-rock and vein-orientation data
- `backend/app/agents/knowledge/historical/gold.md` — Add new districts and production figures
- `.claude/skills/wa-bulletin42-gold.md` — NEW reference skill (citation, nomenclature, district list)

