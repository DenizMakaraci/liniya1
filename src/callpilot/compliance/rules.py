"""Rule knowledge base: YAML files, one per jurisdiction, under rules/.

Each rule is both machine-readable (effect/conditions the PolicyEngine evaluates deterministically)
and human-readable (summary/text the slow-path LLM retrieves and cites).
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Effect = Literal["forbid", "require", "restrict", "advise"]


class Source(BaseModel):
    id: str
    title: str
    url: str = ""


class Hours(BaseModel):
    weekday: tuple[str, str]
    weekend: tuple[str, str]


class Rule(BaseModel):
    id: str
    source: str
    section: str = ""
    summary: str
    text: str = ""
    effect: Effect
    # Conditions (all must match; empty = any)
    directions: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    audiences: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    purposes: list[str] = Field(default_factory=list)
    # Payload
    unless_consent: str | None = None  # consent kind that lifts a "forbid"
    obligation: str | None = None  # machine key, e.g. "recording_notice"
    disclosure: str | None = None  # text template to speak/display; {company}, {address}, {phone}
    hours: Hours | None = None
    confidence: Literal["settled", "likely", "unclear"] = "settled"

    def matches(self, ctx) -> bool:
        return all(
            not allowed or str(value) in allowed
            for allowed, value in (
                (self.directions, ctx.direction),
                (self.modes, ctx.mode),
                (self.audiences, ctx.audience),
                (self.sectors, ctx.sector),
                (self.purposes, ctx.purpose),
            )
        )


class JurisdictionRules(BaseModel):
    jurisdiction: str
    name: str
    inherits: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)


class RuleBook:
    def __init__(self, sets: dict[str, JurisdictionRules]):
        self.sets = sets
        self.by_id: dict[str, Rule] = {r.id: r for s in sets.values() for r in s.rules}
        self.sources: dict[str, Source] = {src.id: src for s in sets.values() for src in s.sources}

    def for_jurisdiction(self, code: str) -> list[Rule]:
        """Rules for a code like 'CA-QC', including inherited sets (CA, then DEFAULT)."""
        seen, out, stack = set(), [], [code]
        while stack:
            c = stack.pop(0)
            if c in seen or c not in self.sets:
                continue
            seen.add(c)
            out.extend(self.sets[c].rules)
            stack.extend(self.sets[c].inherits)
        if "DEFAULT" in self.sets and "DEFAULT" not in seen:
            out.extend(self.sets["DEFAULT"].rules)
        return out

    def all_rules(self) -> list[Rule]:
        return list(self.by_id.values())


def load_rulebook(rules_dir: Path) -> RuleBook:
    sets = {}
    for path in sorted(rules_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        jr = JurisdictionRules.model_validate(data)
        sets[jr.jurisdiction] = jr
    return RuleBook(sets)
