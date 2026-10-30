"""Importers for customer data. Everything lands in the local data dir; nothing is uploaded anywhere.

Contacts CSV columns (header required, order free, extra columns kept in notes):
  number*, name, company, audience(consumer|business), sector, notes,
  consent_kind(written|oral|electronic|callback_request), consent_date(ISO), consent_source,
  consent_number (defaults to number), consent_automated(true|false)

Rows that claim consent without BOTH a date and a source are rejected: CRTC requires the telemarketer
to be able to *demonstrate* express consent, so unevidenced consent is treated as no consent.

Suppression list: one number per line (CSV with a `number` column also accepted). Imported numbers
go to the internal do-not-call list and block all outbound calls.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from typing import TYPE_CHECKING

import phonenumbers
from pydantic import ValidationError

from ..compliance.audit import AuditLog
from ..compliance.models import ConsentEvidence

if TYPE_CHECKING:
    from ..call.memory import ProspectMemory

CONSENT_KINDS = {"written", "oral", "electronic", "callback_request"}
AUDIENCES = {"consumer", "business", "unknown"}


@dataclass
class ImportReport:
    imported: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (row number, reason)
    consents: int = 0

    def summary(self) -> str:
        s = f"imported {self.imported}, consents {self.consents}, rejected {len(self.rejected)}"
        return s + "".join(f"\n  row {n}: {why}" for n, why in self.rejected[:20])


def normalize_number(raw: str, region: str = "CA") -> str | None:
    try:
        p = phonenumbers.parse(raw.strip(), region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(p):
        return None
    return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)


def import_contacts(source: str | Path, memory: "ProspectMemory", region: str = "CA") -> ImportReport:
    text = Path(source).read_text() if isinstance(source, Path) else source
    rep = ImportReport()
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "number" not in [f.strip().lower() for f in reader.fieldnames]:
        rep.rejected.append((0, "missing required 'number' column"))
        return rep
    for i, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        number = normalize_number(row.get("number", ""), region)
        if not number:
            rep.rejected.append((i, f"invalid number {row.get('number')!r}"))
            continue
        audience = row.get("audience", "unknown").lower() or "unknown"
        if audience not in AUDIENCES:
            rep.rejected.append((i, f"audience must be one of {sorted(AUDIENCES)}"))
            continue
        fields = {k: row[k] for k in ("name", "company", "sector", "notes") if row.get(k)}
        fields["audience"] = audience
        extra = {k: v for k, v in row.items() if v and k not in
                 {"number", "name", "company", "audience", "sector", "notes"} and not k.startswith("consent")}
        if extra:
            fields["notes"] = (fields.get("notes", "") + " " + "; ".join(f"{k}={v}" for k, v in extra.items())).strip()

        kind = row.get("consent_kind", "").lower()
        if kind:
            if kind not in CONSENT_KINDS:
                rep.rejected.append((i, f"consent_kind must be one of {sorted(CONSENT_KINDS)}"))
                continue
            if not row.get("consent_date") or not row.get("consent_source"):
                rep.rejected.append((i, "consent claimed without consent_date and consent_source (not demonstrable)"))
                continue
            try:
                captured = datetime.fromisoformat(row["consent_date"])
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=UTC)
                consent = ConsentEvidence(
                    kind=kind, number=normalize_number(row.get("consent_number") or number, region) or number,
                    captured_at=captured, source=row["consent_source"],
                    for_automated_calls=row.get("consent_automated", "false").lower() in ("true", "yes", "1"))
            except (ValueError, ValidationError) as e:
                rep.rejected.append((i, f"bad consent fields: {e}"))
                continue
            if consent.number != number:
                rep.rejected.append((i, "consent_number does not match number (consent is per-number)"))
                continue
            fields["consent"] = consent.model_dump()
            rep.consents += 1
        memory.upsert(number, **fields)
        rep.imported += 1
    return rep


def import_suppression(source: str | Path, audit: AuditLog, region: str = "CA") -> ImportReport:
    text = Path(source).read_text() if isinstance(source, Path) else source
    rep = ImportReport()
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    header = [h.strip().lower() for h in lines[0].split(",")] if lines else []
    if "number" in header:
        numbers = [r.get("number", "") for r in csv.DictReader(io.StringIO("\n".join(lines)))]
    else:
        numbers = [l.split(",")[0] for l in lines]
    for i, raw in enumerate(numbers, start=1):
        if not raw.strip():
            continue
        number = normalize_number(raw, region)
        if not number:
            rep.rejected.append((i, f"invalid number {raw!r}"))
            continue
        if not audit.is_internally_blocked(number):
            audit.opt_out(number, "imported suppression list")
        rep.imported += 1
    return rep
