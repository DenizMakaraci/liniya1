"""InboundSession: the AI answers a call a prospect/customer placed to the business.

Lifecycle
  1. prepare()   gate (direction=inbound, mode=autonomous) -> policy; brief from memory
  2. start()     AI speaks the mandatory opening (identification, AI disclosure, recording notice,
                 Quebec notices) and audits each as disclosure_made by="ai"; then greets
  3. caller turn ASR -> transcript -> deterministic checks (opt-out, human request)
                 -> Speaker reply -> guardrail -> TTS -> "speak" event (+ audio chunks)
                 barge-in: a new final caller transcript cancels any in-flight speech
                 silence: after `silence_timeout` s of nothing, one "still there?" then end
  4. handoff()   on request / regulated topic / repeated guardrail failures / model uncertainty
  5. end()       summary -> memory; consent captured during the call -> memory; audit closed

Outbound callbacks agreed on this call are logged as `callback_request` consent so the outbound
gate (ca.adad.consent etc.) will later allow them.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..compliance import AuditLog, CallContext, Guardrail, PolicyEngine
from ..compliance.engine import CallPolicy
from ..compliance.guardrail import make_llm_judge
from ..compliance.models import ConsentEvidence
from ..config import settings
from ..knowledge import load_kb, load_profile
from ..llm import LegalReasoner, SlowPath, Speaker
from ..llm.speaker import split_tags
from .asr import ASR
from .memory import ProspectMemory
from .session import RETENTION_DAYS, SessionEvent
from .tts import TTS

HUMAN_REQUEST = re.compile(
    r"\b(speak|talk|connect|transfer)\b.{0,25}\b(human|person|agent|someone|representative|manager)\b"
    r"|\b(real person|human being|are you a (robot|bot|machine))\b", re.IGNORECASE)
CALLBACK_YES = re.compile(r"\b(yes|yeah|yep|sure|okay|ok|please|that works|sounds good|go ahead)\b", re.IGNORECASE)
CALLBACK_NO = re.compile(r"\b(no|don'?t|do not|rather not|not now)\b", re.IGNORECASE)
CALLBACK_OFFER = re.compile(r"(call (you )?back|callback).{0,80}\?|\b(permission|may I|can I|okay to) .{0,40}call", re.IGNORECASE)
GOODBYE = re.compile(r"\b(bye|goodbye|that'?s all|that'?s it|have a (good|nice) day)\b", re.IGNORECASE)


@dataclass
class InboundSession:
    ctx: CallContext
    engine: PolicyEngine
    reasoner: LegalReasoner
    asr: ASR
    tts: TTS
    memory: ProspectMemory
    client: Any = None
    assistant_name: str = ""  # defaults to company.yaml assistant_name
    greeting: str = "How can I help you today?"
    silence_timeout: float = 12.0
    max_guardrail_failures: int = 2
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self):
        assert self.ctx.direction == "inbound" and self.ctx.mode == "autonomous", \
            "InboundSession is for AI-answered inbound calls"
        self.audit = AuditLog(settings.data_dir, self.call_id)
        self.profile = load_profile()
        self.kb = load_kb()
        self.speaker = Speaker(self.client, assistant_name=self.assistant_name or self.profile.assistant_name,
                               company_name=self.profile.name, kb=self.kb)
        self.slow = SlowPath(self.client)
        self.transcript: list[dict] = []
        self.policy: CallPolicy | None = None
        self.brief = ""
        self.guardrail: Guardrail | None = None
        self.events: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self.consents: list[ConsentEvidence] = []
        self.outcome: str | None = None
        self._speaking: asyncio.Task | None = None
        self._pending_callback = False
        self._guardrail_failures = 0
        self._unsure = 0
        self._last_caller_at = datetime.now(UTC)
        self._loop_task: asyncio.Task | None = None
        self._silence_task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------------------------

    async def prepare(self) -> CallPolicy:
        self.policy = self.engine.evaluate(self.ctx)
        self.audit.policy_decided(self.policy)
        await self.events.put(SessionEvent("policy", self.policy))
        if self.policy.allowed:
            judge = make_llm_judge(self.client, settings.fast_model) if self.client else None
            self.guardrail = Guardrail(self.policy, self.kb.facts, self.ctx.sector, judge)
            self.brief = await self.slow.build_brief(
                self.memory.as_text(self.ctx.prospect_number), self.guardrail.approved,
                self._compliance_notes(), company=self.profile.as_prompt(),
                knowledge_index=self.kb.overview(), direction="inbound")
            await self.events.put(SessionEvent("brief", self.brief))
        return self.policy

    async def start(self) -> None:
        assert self.policy is not None, "call prepare() first"
        if not self.policy.allowed:
            await self.events.put(SessionEvent("blocked", self.policy.blocking_reasons))
            await self.events.put(None)
            return
        # Mandatory opening, spoken by the AI and audited line by line.
        for obligation, line in self.policy.opening_disclosures:
            await self._say(line, mandatory=True)
            self.audit.disclosure_made(obligation, line, by="ai")
        await self._say(self.greeting, mandatory=False)
        self._loop_task = asyncio.create_task(self._consume_asr())
        self._arm_silence_timer()

    async def feed(self, audio: bytes) -> None:
        await self.asr.feed("caller", audio)

    async def end(self, outcome: str | None = None) -> None:
        if self.outcome:
            return
        self.outcome = outcome or "completed"
        me = asyncio.current_task()
        for t in (self._speaking, self._silence_task):
            if t and t is not me:
                t.cancel()
        await self.asr.close()
        summary = await self.slow.summarize(self.transcript) if self.transcript else "(no transcript)"
        for c in self.consents:
            self.memory.upsert(self.ctx.prospect_number, consent=c.model_dump())
        self.memory.add_interaction(self.ctx.prospect_number, self.call_id, summary, self.outcome,
                                    RETENTION_DAYS)
        self.memory.purge_expired()
        self.audit.call_ended(self.outcome, RETENTION_DAYS)
        await self.events.put(SessionEvent("ended", {"summary": summary, "outcome": self.outcome,
                                                      "consents": [c.model_dump() for c in self.consents],
                                                      "audit": self.audit.path.name}))
        await self.events.put(None)

    async def handoff(self, reason: str) -> None:
        self.audit.event("handoff", reason=reason)
        await self._say("One moment please, I'm connecting you with a colleague.", mandatory=True)
        await self.events.put(SessionEvent("handoff", {"reason": reason, "number": self.ctx.prospect_number,
                                                        "transcript": self.transcript}))
        await self.end("handoff")

    async def stream(self) -> AsyncIterator[SessionEvent]:
        while (e := await self.events.get()) is not None:
            yield e

    # -- turn loop -------------------------------------------------------------------------

    async def _consume_asr(self):
        async for t in self.asr.results():
            if self.outcome:
                break
            if not t.is_final or not t.text:
                continue
            self._interrupt()  # barge-in
            self._last_caller_at = datetime.now(UTC)
            self.transcript.append({"speaker": "caller", "text": t.text})
            await self.events.put(SessionEvent("transcript", t))
            await self._handle_caller_turn(t.text)
            self._arm_silence_timer()

    async def _handle_caller_turn(self, text: str):
        if self.guardrail.prospect_opted_out(text):
            self.audit.opt_out(self.ctx.prospect_number, text)
            await self.events.put(SessionEvent("opt_out", {"text": text}))
            await self._say("Understood, I've noted that you don't want to be contacted. Goodbye.", mandatory=True)
            return await self.end("caller_opted_out")
        if HUMAN_REQUEST.search(text):
            return await self.handoff("caller requested a human")
        if len(self.transcript) > 2 and GOODBYE.search(text):
            await self._say("Thanks for calling, have a good day. Goodbye.", mandatory=False)
            return await self.end("completed")
        # Consent only counts when WE asked permission on the previous turn and the caller affirms.
        if self._pending_callback and CALLBACK_YES.search(text) and not CALLBACK_NO.search(text):
            self._capture_callback_consent(text)
        self._speaking = asyncio.create_task(self._respond())
        try:
            await self._speaking
        except asyncio.CancelledError:
            pass

    async def _respond(self):
        raw = "".join([c async for c in self.speaker.reply(
            self.brief, self.guardrail.approved, self._compliance_notes(), self.transcript)])
        text, tags = split_tags(raw)
        res = await self.guardrail.check(text)
        self.audit.guardrail(res, text)
        if not res.allowed and res.text == text:
            self._guardrail_failures += 1
            if self._guardrail_failures >= self.max_guardrail_failures:
                return await self.handoff(f"guardrail blocked {self._guardrail_failures} replies: {res.violations}")
            text = "Let me have a colleague confirm that for you. Is there anything else I can help with?"
        else:
            text = res.text
        # The AI asked for permission to call back -> the caller's next "yes" is consent.
        self._pending_callback = bool(CALLBACK_OFFER.search(text))
        await self._say(text, mandatory=False, violations=res.violations if not res.allowed else None)
        if "HANDOFF" in tags:
            return await self.handoff("assistant requested handoff")
        if "END" in tags:
            return await self.end("completed")

    # -- speaking --------------------------------------------------------------------------

    async def _say(self, text: str, mandatory: bool, violations=None):
        self.transcript.append({"speaker": "ai", "text": text})
        payload = {"text": text, "mandatory": mandatory}
        if violations:
            payload["violations"] = violations
        await self.events.put(SessionEvent("speak", payload))
        async for chunk in self.tts.synthesize(text):
            await self.events.put(SessionEvent("audio", chunk))

    def _interrupt(self):
        if self._speaking and not self._speaking.done():
            self._speaking.cancel()
            self.audit.event("barge_in")

    def _arm_silence_timer(self):
        if self._silence_task:
            self._silence_task.cancel()
        self._silence_task = asyncio.create_task(self._silence_watch())

    async def _silence_watch(self):
        await asyncio.sleep(self.silence_timeout)
        if self.outcome:
            return
        await self._say("Are you still there?", mandatory=False)
        await asyncio.sleep(self.silence_timeout / 2)
        if not self.outcome:
            await self._say("I'll let you go. Feel free to call back any time. Goodbye.", mandatory=False)
            await self.end("caller_silent")

    # -- consent / compliance --------------------------------------------------------------

    def _capture_callback_consent(self, utterance: str):
        c = ConsentEvidence(kind="callback_request", number=self.ctx.prospect_number,
                            captured_at=datetime.now(UTC), source=f"inbound call {self.call_id}: {utterance!r}",
                            for_automated_calls=True)
        self.consents.append(c)
        self._pending_callback = False
        self.audit.consent_captured(c)

    def _compliance_notes(self) -> str:
        p = self.policy
        notes = [f"Inbound call, jurisdiction {p.jurisdiction} ({p.jurisdiction_description}).",
                 f"Caller ID is {self.ctx.prospect_number}; if they say 'this number', use it and don't ask again.",
                 "Opening disclosures already made. Don't repeat yourself; if the caller already agreed to"
                 + " something, confirm it once and move on."]
        if p.requires("approved_claims_only"):
            notes.append("Only approved facts; no prices or stats not listed.")
        if p.requires("no_regulated_advice"):
            notes.append(f"No individual {self.ctx.sector} advice; offer a licensed human.")
        if p.requires("automated_decision_notice"):
            notes.append("If you make a decision about the caller, remind them they can ask for human review.")
        if p.requires("french_available"):
            notes.append("If the caller speaks French, continue in French.")
        if p.requires("casl_followup_consent"):
            notes.append("Ask permission before promising any follow-up email or text.")
        return " ".join(notes)
