"""Append-only JSONL compliance log (one file per call) plus internal do-not-call list.

Records exactly what a regulator or privacy officer asks for after the fact: which rules were
applied, what disclosures were actually made and when, consent evidence, guardrail interventions,
opt-outs, and retention decisions. Stored locally (OpenShell volume), never sent to the model.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, data_dir: Path, call_id: str):
        self.dir = data_dir / "audit"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{call_id}.jsonl"
        self.call_id = call_id
        self.internal_dncl = data_dir / "internal_dncl.txt"

    def event(self, kind: str, **data: Any) -> None:
        rec = {"ts": datetime.now(UTC).isoformat(), "call_id": self.call_id, "event": kind, **data}
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def policy_decided(self, policy) -> None:
        self.event("policy_decided", allowed=policy.allowed, jurisdiction=policy.jurisdiction,
                   blocking=policy.blocking_reasons, obligations=sorted(policy.obligations),
                   rules=[r.id for r in policy.applied_rules])

    def disclosure_made(self, obligation: str, text: str, by: str) -> None:
        self.event("disclosure_made", obligation=obligation, text=text, by=by)

    def consent_captured(self, consent) -> None:
        self.event("consent_captured", **consent.model_dump())

    def guardrail(self, result, original: str) -> None:
        self.event("guardrail", allowed=result.allowed, violations=result.violations,
                   original=original, final=result.text)

    def opt_out(self, number: str, utterance: str) -> None:
        self.event("opt_out", number=number, utterance=utterance)
        with self.internal_dncl.open("a") as f:
            f.write(f"{number}\t{datetime.now(UTC).isoformat()}\n")

    def call_ended(self, outcome: str, retention_days: int) -> None:
        self.event("call_ended", outcome=outcome, retention_days=retention_days,
                   delete_after=(datetime.now(UTC) + timedelta(days=retention_days)).date().isoformat())

    def is_internally_blocked(self, number: str) -> bool:
        if not self.internal_dncl.exists():
            return False
        return any(line.split("\t")[0] == number for line in self.internal_dncl.read_text().splitlines())

    def read(self) -> list[dict]:
        return [json.loads(l) for l in self.path.read_text().splitlines()] if self.path.exists() else []
