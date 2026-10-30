"""Live output guardrail.

Two layers, both fast enough for the speech loop:
1. Deterministic regex checks (sub-millisecond) for the things that are never OK.
2. Optional LLM judge (fast model, JSON mode) that verifies factual/pricing claims against the
   approved-claims list. Skipped when no client is configured.

Also watches the *prospect's* side for opt-out intent, which must end the call.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .engine import CallPolicy

OPTOUT_PATTERNS = [
    r"\b(stop|quit|don'?t|do not|never)\b.{0,20}\b(call|contact|phone)",
    r"\b(remove|take) me (off|from)",
    r"\bnot interested\b.{0,30}\b(call|again)",
    r"\bdo[- ]not[- ]call\b",
    r"\bunsubscribe\b",
]

# Always-blocked content regardless of sector.
HARD_BLOCK = {
    "guarantee": r"\b(guarantee[ds]?|risk[- ]free|can'?t lose|100 ?%)\b",
    "fake_urgency": r"\b(only today|last chance|offer expires (today|tonight|in \d+ (minutes|hours)))\b",
    "impersonation": r"\b(calling from|on behalf of) (the )?(government|cra|irs|revenue agency|your bank|police)\b",
}

# Needs grounding in approved claims.
CLAIM_PATTERNS = {
    "pricing": r"(\$|€|£|usd|cad|eur)\s?\d|\b\d+\s?(dollars|percent|%)",
    "statistic": r"\b\d+(\.\d+)?\s?(%|percent|x|times)\b",
    "superlative": r"\b(best|cheapest|fastest|#1|number one|leading)\b",
}

REGULATED_ADVICE = {
    "finance": r"\b(you should (invest|buy|sell)|guaranteed return|tax[- ]free)\b",
    "insurance": r"\b(you (are|'re) covered|this policy will pay)\b",
    "health": r"\b(this (cures|treats)|you (don'?t )?need (to see|a doctor))\b",
    "legal": r"\b(you (will|won'?t) be liable|legally you (must|can))\b",
}


class GuardrailResult(BaseModel):
    allowed: bool
    text: str  # possibly rewritten
    violations: list[str] = Field(default_factory=list)
    needs_grounding: list[str] = Field(default_factory=list)


class Guardrail:
    def __init__(self, policy: CallPolicy, approved_claims: list[str], sector: str = "general",
                 llm_judge=None):
        self.policy = policy
        self.approved = [c for c in approved_claims if c.strip() and not c.startswith("#")]
        self.sector = sector
        self.judge = llm_judge  # async callable(text, approved) -> dict

    @classmethod
    def load_claims(cls, path: Path | None = None) -> list[str]:
        """Approved facts from knowledge/facts.md (or an explicit file)."""
        from ..knowledge.kb import load_kb, parse_facts

        if path is not None:
            return parse_facts(path.read_text()) if path.exists() else []
        return load_kb().facts

    def prospect_opted_out(self, transcript: str) -> bool:
        t = transcript.lower()
        return any(re.search(p, t) for p in OPTOUT_PATTERNS)

    def check_sync(self, text: str) -> GuardrailResult:
        t = text.lower()
        violations = [f"hard_block:{k}" for k, p in HARD_BLOCK.items() if re.search(p, t)]
        if self.policy.requires("no_regulated_advice"):
            pat = REGULATED_ADVICE.get(self.sector)
            if pat and re.search(pat, t):
                violations.append(f"regulated_advice:{self.sector}")
        grounding = [k for k, p in CLAIM_PATTERNS.items() if re.search(p, t)]
        return GuardrailResult(allowed=not violations, text=text, violations=violations,
                               needs_grounding=grounding)

    async def check(self, text: str) -> GuardrailResult:
        res = self.check_sync(text)
        if not res.allowed or not res.needs_grounding:
            return res
        if not self.policy.requires("approved_claims_only"):
            return res
        if self.judge is None:
            # No LLM available: be conservative and require the claim to appear verbatim-ish.
            if not any(_overlap(text, c) for c in self.approved):
                res.allowed = False
                res.violations.append("ungrounded_claim")
            return res
        verdict = await self.judge(text, self.approved)
        if not verdict.get("grounded", False):
            res.allowed = False
            res.violations.append(f"ungrounded_claim:{verdict.get('reason', '')}")
            if verdict.get("rewrite"):
                res.text = verdict["rewrite"]
        return res


JUDGE_SYSTEM = (
    "You are a compliance checker for sales calls. Given a proposed sentence and a list of approved "
    "product facts, decide whether every factual, numeric or pricing claim in the sentence is supported "
    "by the approved facts. Reply with JSON only: "
    '{"grounded": bool, "reason": str, "rewrite": str|null} where rewrite is a compliant version '
    "that drops or softens unsupported claims (null if grounded)."
)


def make_llm_judge(client, model: str):
    async def judge(text: str, approved: list[str]) -> dict:
        msg = "Approved facts:\n- " + "\n- ".join(approved) + f"\n\nProposed sentence: {text}"
        from ..llm.client import complete

        raw = await complete(
            client, model, think=False, temperature=0, max_tokens=200,
            messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": msg}],
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate prose or code fences around the JSON
        try:
            return json.loads(m.group(0)) if m else {"grounded": False, "reason": "judge returned no JSON"}
        except json.JSONDecodeError:
            return {"grounded": False, "reason": "judge returned invalid JSON"}

    return judge


def _overlap(text: str, claim: str, threshold: float = 0.5) -> bool:
    a, b = set(re.findall(r"\w+", text.lower())), set(re.findall(r"\w+", claim.lower()))
    return bool(b) and len(a & b) / len(b) >= threshold
