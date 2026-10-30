"""Slow path: big Nemotron with reasoning ON. Runs before the call and between turns, never
inside the audio loop. Produces the call brief and refreshes it as the conversation evolves.
"""

from ..config import settings
from .client import complete

BRIEF_SYSTEM = """You prepare a concise call brief. Use ONLY the provided company profile, prospect
memory, approved facts and knowledge index. Output sections: Goal, Prospect context (3 bullets max),
Likely objections or questions + responses (3 max), Questions to ask (3 max), Do-not-say (from
compliance). For an INBOUND call the goal is to help the caller with what they called about, not to
pitch. Plain text, under 250 words."""

REFRESH_SYSTEM = """Update the call brief given the live transcript so far. Keep the same sections,
keep it under 250 words, and add a 'Live read' section with the prospect's current stance."""


class SlowPath:
    def __init__(self, client, model: str | None = None):
        self.client = client
        self.model = model or settings.slow_model

    async def _run(self, system: str, user: str, max_tokens: int = 700, think: bool = True) -> str:
        if self.client is None:
            return "(no LLM configured) Goal: book a 15-minute demo. Ask about current scheduling pain."
        return await complete(
            self.client, self.model, think=think, temperature=0.2, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )

    async def build_brief(self, prospect_memory: str, approved_claims: list[str],
                          compliance_notes: str, company: str = "", knowledge_index: str = "",
                          direction: str = "outbound") -> str:
        user = (f"CALL DIRECTION: {direction}\n\nCOMPANY PROFILE:\n{company or '(not set)'}\n\n"
                f"PROSPECT MEMORY:\n{prospect_memory or '(first contact)'}\n\n"
                f"APPROVED FACTS:\n- " + "\n- ".join(approved_claims) +
                f"\n\nKNOWLEDGE INDEX (topics the assistant can look up):\n{knowledge_index or '(none)'}"
                f"\n\nCOMPLIANCE NOTES:\n{compliance_notes}")
        return await self._run(BRIEF_SYSTEM, user, think=False)

    async def refresh_brief(self, brief: str, transcript: list[dict]) -> str:
        turns = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in transcript)
        return await self._run(REFRESH_SYSTEM, f"CURRENT BRIEF:\n{brief}\n\nTRANSCRIPT:\n{turns}", think=False)

    async def summarize(self, transcript: list[dict]) -> str:
        turns = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in transcript)
        return await self._run(
            "Summarize this sales call in 5 bullets: outcome, needs, objections, next step, "
            "any consent or opt-out statements verbatim.", turns, max_tokens=300, think=False)
