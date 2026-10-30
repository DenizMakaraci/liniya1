"""CopilotSession: orchestrates one outbound call where the human speaks and the AI whispers.

Lifecycle
  1. prepare()   slow path: jurisdiction gate -> policy; legal explanation; call brief
  2. start()     refuses if policy.blocked; emits the mandatory opening script to the rep
  3. feed audio  ASR -> transcript -> (prospect turn) fast path whisper -> guardrail -> emit
                 every ~6 turns the slow path refreshes the brief in the background
  4. end()       summary -> memory; audit "call_ended" with retention; purge expired data
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..compliance import AuditLog, CallContext, Guardrail, PolicyEngine
from ..compliance.engine import CallPolicy
from ..compliance.guardrail import make_llm_judge
from ..config import settings
from ..knowledge import load_kb, load_profile
from ..llm import FastPath, LegalReasoner, SlowPath
from .asr import ASR
from .memory import ProspectMemory

RETENTION_DAYS = 3 * 365  # CRTC: keep telemarketing records three years


@dataclass
class SessionEvent:
    kind: str  # policy | legal | brief | transcript | whisper | blocked | opt_out | ended | error
    data: Any = None


@dataclass
class CopilotSession:
    ctx: CallContext
    engine: PolicyEngine
    reasoner: LegalReasoner
    asr: ASR
    memory: ProspectMemory
    client: Any = None
    rep_name: str = "the rep"
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self):
        self.audit = AuditLog(settings.data_dir, self.call_id)
        self.profile = load_profile()
        self.kb = load_kb()
        self.fast = FastPath(self.client)
        self.slow = SlowPath(self.client)
        self.transcript: list[dict] = []
        self.policy: CallPolicy | None = None
        self.brief = ""
        self.guardrail: Guardrail | None = None
        self.events: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._disclosed: set[str] = set()
        self._bg: set[asyncio.Task] = set()

    # -- lifecycle -------------------------------------------------------------------------

    async def prepare(self) -> CallPolicy:
        self.policy = self.engine.evaluate(self.ctx)
        self.audit.policy_decided(self.policy)
        await self.events.put(SessionEvent("policy", self.policy))

        legal = await self.reasoner.explain_policy(self.policy)
        await self.events.put(SessionEvent("legal", legal))

        if self.policy.allowed:
            judge = make_llm_judge(self.client, settings.fast_model) if self.client else None
            self.guardrail = Guardrail(self.policy, self.kb.facts, self.ctx.sector, judge)
            self.brief = await self.slow.build_brief(
                self.memory.as_text(self.ctx.prospect_number), self.guardrail.approved,
                self._compliance_notes(), company=self.profile.as_prompt(),
                knowledge_index=self.kb.overview(), direction=str(self.ctx.direction))
            await self.events.put(SessionEvent("brief", self.brief))
        return self.policy

    async def start(self) -> None:
        assert self.policy is not None, "call prepare() first"
        if not self.policy.allowed:
            await self.events.put(SessionEvent("blocked", self.policy.blocking_reasons))
            await self.events.put(None)
            return
        await self.events.put(SessionEvent("whisper", {"text": " ".join(self.policy.opening_script),
                                                        "mandatory": True}))
        asyncio.create_task(self._consume_asr())

    async def feed(self, speaker: str, audio: bytes) -> None:
        await self.asr.feed(speaker, audio)

    async def end(self, outcome: str = "completed") -> None:
        await self.asr.close()
        for t in self._bg:
            t.cancel()
        summary = await self.slow.summarize(self.transcript) if self.transcript else "(no transcript)"
        self.memory.add_interaction(self.ctx.prospect_number, self.call_id, summary, outcome, RETENTION_DAYS)
        self.memory.purge_expired()
        self.audit.call_ended(outcome, RETENTION_DAYS)
        await self.events.put(SessionEvent("ended", {"summary": summary, "audit": self.audit.path.name}))
        await self.events.put(None)

    async def stream(self) -> AsyncIterator[SessionEvent]:
        while (e := await self.events.get()) is not None:
            yield e

    # -- internals -------------------------------------------------------------------------

    async def _consume_asr(self):
        async for t in self.asr.results():
            if not t.is_final or not t.text:
                continue
            self.transcript.append({"speaker": t.speaker, "text": t.text})
            await self.events.put(SessionEvent("transcript", t))
            if t.speaker == "rep":
                self._track_disclosures(t.text)
                continue
            if self.guardrail.prospect_opted_out(t.text):
                self.audit.opt_out(self.ctx.prospect_number, t.text)
                await self.events.put(SessionEvent("opt_out", {
                    "text": "Prospect asked not to be contacted. Say: 'Understood, I've removed your number. "
                            "Have a good day.' and end the call.", "mandatory": True}))
                continue
            await self._whisper()
            if len(self.transcript) % 6 == 0:
                task = asyncio.create_task(self._refresh_brief())
                self._bg.add(task)
                task.add_done_callback(self._bg.discard)

    async def _whisper(self):
        text = "".join([c async for c in self.fast.suggest(self.brief, self._compliance_notes(), self.transcript)])
        res = await self.guardrail.check(text)
        self.audit.guardrail(res, text)
        if res.allowed:
            await self.events.put(SessionEvent("whisper", {"text": res.text, "mandatory": False}))
        elif res.text != text:  # judge rewrote it
            await self.events.put(SessionEvent("whisper", {"text": res.text, "mandatory": False,
                                                            "rewritten_from": text, "violations": res.violations}))
        else:
            await self.events.put(SessionEvent("whisper", {
                "text": "Acknowledge and ask what matters most to them right now.",
                "mandatory": False, "suppressed": text, "violations": res.violations}))

    async def _refresh_brief(self):
        try:
            self.brief = await self.slow.refresh_brief(self.brief, self.transcript)
            await self.events.put(SessionEvent("brief", self.brief))
        except Exception as e:  # background: never break the live loop
            await self.events.put(SessionEvent("error", f"brief refresh failed: {e}"))

    def _track_disclosures(self, rep_text: str):
        """Mark opening-script obligations as satisfied when the rep actually says them."""
        t = rep_text.lower()
        checks = {
            "identify_caller": self.profile.name.lower() in t,
            "recording_notice": bool(re.search(r"\brecord", t)),
            "ai_disclosure": bool(re.search(r"\b(ai|artificial intelligence|automated|assistant)\b", t)),
            "purpose_notice": "privacy" in t,
        }
        for ob, hit in checks.items():
            if hit and ob in self.policy.obligations and ob not in self._disclosed:
                self._disclosed.add(ob)
                self.audit.disclosure_made(ob, rep_text, by="rep")

    def _compliance_notes(self) -> str:
        p = self.policy
        notes = [f"Jurisdiction: {p.jurisdiction} ({p.jurisdiction_description})."]
        pending = [o for o in ("identify_caller", "recording_notice", "purpose_notice")
                   if o in p.obligations and o not in self._disclosed]
        if pending:
            notes.append("Still to be said by the rep before pitching: " + ", ".join(pending) + ".")
        if p.requires("approved_claims_only"):
            notes.append("Only approved product facts; no prices or stats not in the brief.")
        if p.requires("no_regulated_advice"):
            notes.append(f"No individual {self.ctx.sector} advice; offer a licensed follow-up.")
        if p.requires("honour_optout"):
            notes.append("If asked to stop calling: agree, confirm removal, end call.")
        if p.calling_window:
            notes.append(f"Calling window {p.calling_window}.")
        return " ".join(notes)
