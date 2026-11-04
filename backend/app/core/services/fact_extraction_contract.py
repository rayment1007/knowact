"""M1 provider output: narrative context plus independently reviewed dates.

All fields are required, including explicit nulls. This makes the contract
usable by strict structured-output parsers without default-filled omissions.
Source grounding, date precision and bounded content are service decisions;
schema-conforming AI output alone is never permission to approve a fact.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AtomicDateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fact_type: Literal["MEETING_TIME"]
    subject: str
    occurrence: str | None
    value_date: str | None
    original_value: str
    modality: Literal["ASSERTION", "PROPOSED_CHANGE", "QUESTION", "UNCERTAIN"]
    previous_value_date: str | None
    change_cue: str | None
    evidence_text: str
    source_context: Literal["DIRECT", "QUOTED", "FORWARDED", "UNCLEAR"]


class FactBundleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str
    key_points: list[str]
    evidence_text: str
    facts: list[AtomicDateCandidate]


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_MONTH_PATTERN = "(?:" + "|".join(f"{month}(?:)?|{month[:3]}" for month in _MONTHS) + ")"
_DATE_PATTERN = re.compile(
    rf"(?<!\w)(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+{_MONTH_PATTERN}\s+\d{{4}}"
    rf"|{_MONTH_PATTERN}\s+\d{{1,2}},?\s+\d{{4}})(?!\w)", re.I,
)


def _complete_date(text: str) -> str | None:
    """Read only an explicit complete date, never infer a missing year."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        parts = text.replace(",", "").split()
        if parts[0].isdigit():
            day, month, year = int(parts[0]), parts[1], int(parts[2])
        else:
            month, day, year = parts[0], int(parts[1]), int(parts[2])
        month_number = next(i for i, name in enumerate(_MONTHS, 1)
                            if month.casefold() in {name.casefold(), name[:3].casefold()})
        return date(year, month_number, day).isoformat()
    except (ValueError, IndexError, StopIteration):
        return None


def mock_fact_bundle(content: str, title: str, context: dict) -> FactBundleOutput:
    """Conservative offline fixture provider; not a semantic-quality baseline.

    It recognizes explicit English UAT/meeting dates only. Other fact types,
    missing years and invalid dates produce no atomic dates. Quotes and
    proposals remain labelled candidates; they are never promoted to facts.
    """
    passages = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\r?\n", content) if part.strip()]
    first = passages[0] if passages else content.strip()
    evidence = first[:1000]
    candidates = []
    forwarded = False
    quoted = False
    for passage in passages:
        if re.search(r"forwarded message|begin forwarded|original message", passage, re.I):
            forwarded = True
        if re.search(r"^On .+wrote:\s*$", passage):
            quoted = True
        subject_match = re.search(
            r"\b(?:UAT(?:\s+(?:review|sign-off|testing))?(?:\s+meeting)?"
            r"|(?:(?:project|team|review|planning|kickoff|standup)\s+)?meeting)\b", passage, re.I)
        if subject_match is None:
            continue
        dates = [(match, _complete_date(match.group())) for match in _DATE_PATTERN.finditer(passage)]
        dates = [(match, parsed) for match, parsed in dates if parsed is not None]
        if not dates:
            continue
        context_kind = ("FORWARDED" if forwarded else "QUOTED" if quoted or passage.startswith(">") else "DIRECT")
        if re.search(r"\b(?:not|never|cancelled|canceled|uncertain|unconfirmed)\b", passage, re.I):
            modality = "UNCERTAIN"
        elif "?" in passage:
            modality = "QUESTION"
        elif re.search(r"\b(?:propos\w*|suggest\w*|might|may|could|should|would)\b", passage, re.I):
            modality = "PROPOSED_CHANGE"
        else:
            modality = "ASSERTION"
        cue = re.search(r"\b(?:rescheduled|moved|changed)\s+from\b", passage, re.I)
        replacement = bool(cue and len(dates) == 2 and re.fullmatch(
            r"\s+to\s+", passage[dates[0][0].end():dates[1][0].start()], re.I))
        if len(dates) > 1 and not replacement:
            modality = "UNCERTAIN"
        chosen = dates[-1] if replacement else dates[0]
        occurrence_match = re.search(r"\b(?:release|round|sprint)\s+[A-Za-z0-9.-]+\b", passage, re.I)
        candidates.append(AtomicDateCandidate(
            fact_type="MEETING_TIME", subject=subject_match.group(),
            occurrence=occurrence_match.group() if occurrence_match else None,
            value_date=chosen[1] if len(dates) == 1 or replacement else None,
            original_value=chosen[0].group(), modality=modality,
            previous_value_date=dates[0][1] if replacement else None,
            change_cue=cue.group() if replacement else None,
            evidence_text=passage, source_context=context_kind,
        ))
        if len(candidates) >= 12:
            break
    return FactBundleOutput(summary=evidence[:500], key_points=[part[:300] for part in passages[:3]],
        evidence_text=evidence, facts=candidates)
