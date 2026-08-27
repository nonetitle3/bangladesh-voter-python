"""Automatic quality checks for Bengali voter extraction.

The quality layer deliberately does not correct personal names with a
hard-coded dictionary. It detects structural anomalies so large PDFs can be
processed automatically and only genuinely suspicious records/pages need
review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Iterable

BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
DIGIT_RE = re.compile(r"[0-9০-৯]")
BAD_GLYPH_RE = re.compile(r"[ŐýƁƀƄƣËŘŞ×ĥėÎÏÐÑÒÓÔÕÖØÙÚÜÝÞß�]")
SERIAL_RE = re.compile(r"^[0-9]{1,5}$")
VOTER_ID_RE = re.compile(r"^[0-9]{6,20}$")
DATE_RE = re.compile(r"^(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}$|^\d{1,2}[-/]\d{1,2}[-/]\d{4}$")

PERSON_FIELDS = ("name", "father_name", "mother_name")
SEARCH_FIELDS = PERSON_FIELDS + ("address", "district", "upazila", "union_name", "occupation")


@dataclass(frozen=True)
class QualityResult:
    score: float
    status: str
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bengali_ratio(value: str) -> float:
    if not value:
        return 0.0
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return 0.0
    return sum(bool(BENGALI_RE.fullmatch(c)) for c in letters) / len(letters)


def _has_bad_glyphs(value: str) -> bool:
    return bool(BAD_GLYPH_RE.search(value))


def _valid_date(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, date):
        return date(1900, 1, 1) <= value <= date.today()
    return bool(DATE_RE.fullmatch(_text(value)))


def score_record(record: dict[str, Any]) -> QualityResult:
    """Score a record using format and consistency checks only."""
    issues: list[str] = []
    score = 1.0

    name = _text(record.get("name"))
    voter_id = _text(record.get("voter_id"))
    serial = _text(record.get("serial_no"))
    raw = _text(record.get("raw_text"))

    if not name:
        issues.append("missing_name")
        score -= 0.28
    elif len(name) < 2:
        issues.append("short_name")
        score -= 0.10
    elif _bengali_ratio(name) < 0.30 and not re.search(r"[A-Za-z]", name):
        issues.append("unusual_name_characters")
        score -= 0.12

    if voter_id:
        if not VOTER_ID_RE.fullmatch(voter_id):
            issues.append("invalid_voter_id_format")
            score -= 0.16
    else:
        issues.append("missing_voter_id")
        score -= 0.06

    if serial and not SERIAL_RE.fullmatch(serial):
        issues.append("invalid_serial")
        score -= 0.08

    if not any(_text(record.get(k)) for k in ("father_name", "mother_name", "address")):
        issues.append("missing_supporting_fields")
        score -= 0.14

    if not _valid_date(record.get("birth_date")):
        issues.append("invalid_birth_date")
        score -= 0.12

    for field in SEARCH_FIELDS:
        value = _text(record.get(field))
        if value and _has_bad_glyphs(value):
            issues.append(f"bad_glyph:{field}")
            score -= 0.20

    if raw and _has_bad_glyphs(raw):
        issues.append("bad_glyph_in_raw_text")
        score -= 0.15

    if raw:
        control_count = sum(ord(c) < 32 and c not in "\n\r\t" for c in raw)
        if control_count:
            issues.append("control_characters")
            score -= 0.08

    score = max(0.0, min(1.0, score))

    if score >= 0.90 and not issues:
        status = "high"
    elif score >= 0.75:
        status = "normal"
    else:
        status = "review"

    return QualityResult(round(score, 4), status, tuple(issues))


def merge_confidence(record: dict[str, Any]) -> QualityResult:
    """Combine extractor confidence with structural quality without rewriting text."""
    result = score_record(record)
    extractor_confidence = float(record.get("confidence") or 0.0)

    if extractor_confidence > 0:
        combined = round((extractor_confidence * 0.65) + (result.score * 0.35), 4)
    else:
        combined = result.score

    if combined >= 0.90 and not result.issues:
        status = "high"
    elif combined >= 0.75:
        status = "normal"
    else:
        status = "review"

    return QualityResult(combined, status, result.issues)


def page_quality(records: Iterable[dict[str, Any]], page_number: int | None = None) -> dict[str, Any]:
    """Produce an automatic page-level anomaly report."""
    rows = list(records)
    results = [score_record(row) for row in rows]
    suspicious = [r for r in results if r.status == "review"]

    issue_counts: dict[str, int] = {}
    for result in results:
        for issue in result.issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    average = sum(r.score for r in results) / len(results) if results else 0.0
    return {
        "page": page_number,
        "records": len(rows),
        "review_records": len(suspicious),
        "average_score": round(average, 4),
        "status": "review" if suspicious else "ok",
        "issues": dict(sorted(issue_counts.items(), key=lambda item: (-item[1], item[0]))),
    }


def document_quality(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate quality information for an entire document."""
    rows = list(records)
    pages: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        page = int(row.get("page_number") or 0)
        pages.setdefault(page, []).append(row)

    page_reports = [page_quality(page_rows, page) for page, page_rows in sorted(pages.items())]
    review_records = sum(p["review_records"] for p in page_reports)
    average = sum(p["average_score"] * p["records"] for p in page_reports)
    average = average / len(rows) if rows else 0.0

    return {
        "records": len(rows),
        "pages_with_records": len(page_reports),
        "review_records": review_records,
        "average_score": round(average, 4),
        "status": "review" if review_records else "ok",
        "pages": page_reports,
    }
