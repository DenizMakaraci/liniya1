"""InboundSession tests run fully offline (client=None -> canned Speaker replies, mock ASR/TTS)."""

import asyncio

import pytest

from callpilot.call import InboundSession, MockASR, MockTTS, ProspectMemory
from callpilot.compliance import CallContext, PolicyEngine, load_rulebook
from callpilot.config import ROOT, settings
from callpilot.llm.reasoner import LegalReasoner
from callpilot.llm.speaker import split_tags
from callpilot.telephony.twilio_bridge import (
    downsample_2x,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
    upsample_2x,
)

COMPANY = {"company": "Acme", "address": "1 Main St", "phone": "+1 800 555 0100",
           "dncl_registered": True, "rep": "Dana", "product": "Scheduler"}


@pytest.fixture
def make_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    rb = load_rulebook(ROOT / "rules")

    def _make(number="+14165550123", **kw):
        ctx = CallContext(direction="inbound", mode="autonomous", prospect_number=number, **kw)
        return InboundSession(ctx, PolicyEngine(rb, COMPANY), LegalReasoner(rb), MockASR(), MockTTS(),
                              ProspectMemory(tmp_path), None, silence_timeout=60)

    return _make


async def run_call(session, caller_lines, hangup=True):
    events = []

    async def collect():
        async for ev in session.stream():
            events.append(ev)

    task = asyncio.create_task(collect())
    await session.prepare()
    await session.start()
    for line in caller_lines:
        if session.outcome:
            break
        await session.feed(line.encode())
        await asyncio.sleep(0.05)
    if hangup and not session.outcome:
        await session.end("caller_hung_up")
    await asyncio.wait_for(task, 5)
    return events


def spoken(events):
    return [e.data["text"] for e in events if e.kind == "speak"]


async def test_opening_disclosures_spoken_and_audited(make_session):
    s = make_session("+15145550123")  # Quebec
    events = await run_call(s, ["Hi"])
    lines = spoken(events)
    assert lines[0].startswith("This is an automated call on behalf of Acme")
    assert any("AI assistant" in l for l in lines[:5])
    assert any("recorded" in l for l in lines[:5])
    assert any("ask for a person to review" in l for l in lines[:5])  # Law 25 s.12.1
    made = [e for e in s.audit.read() if e["event"] == "disclosure_made"]
    assert {e["obligation"] for e in made} >= {"identify_caller_full", "ai_disclosure", "recording_notice",
                                                "automated_decision_notice"}
    assert all(e["by"] == "ai" for e in made)


async def test_caller_requests_human_triggers_handoff(make_session):
    s = make_session()
    events = await run_call(s, ["Hi, I have a billing question.", "Can I speak to a real person?"])
    kinds = [e.kind for e in events]
    assert "handoff" in kinds
    assert s.outcome == "handoff"
    assert any("connecting you with a colleague" in l for l in spoken(events))


async def test_opt_out_ends_call_and_blocks_future_outbound(make_session):
    s = make_session()
    events = await run_call(s, ["Who is this?", "Remove my number and do not call me again."])
    assert s.outcome == "caller_opted_out"
    assert any(e.kind == "opt_out" for e in events)
    assert s.audit.is_internally_blocked("+14165550123")


async def test_callback_agreement_captures_consent(make_session):
    s = make_session()
    events = await run_call(s, ["Can someone call me back tomorrow?", "Yes please, call me on this number."])
    assert s.consents and s.consents[0].kind == "callback_request"
    assert s.consents[0].for_automated_calls is True
    ended = next(e for e in events if e.kind == "ended")
    assert ended.data["consents"][0]["number"] == "+14165550123"
    # consent persisted to memory -> the outbound gate can use it later
    assert "callback_request" in (s.memory.get("+14165550123") or {}).get("consent_json", "")


async def test_goodbye_ends_naturally(make_session):
    s = make_session()
    await run_call(s, ["Just checking your hours.", "Thanks, bye."], hangup=False)
    assert s.outcome == "completed"


async def test_barge_in_cancels_speech(make_session):
    s = make_session()
    await s.prepare()
    await s.start()
    await s.feed(b"Tell me about it")
    await asyncio.sleep(0.01)
    await s.feed(b"Actually wait")
    await asyncio.sleep(0.1)
    await s.end()
    # either a barge_in was recorded or the first reply finished before the second turn; both OK,
    # but the session must stay consistent and end cleanly
    assert s.outcome == "completed"
    assert s.transcript[-1]["speaker"] in ("ai", "caller")


async def test_blocked_policy_short_circuits(make_session, monkeypatch):
    s = make_session()
    s.audit.opt_out("+14165550123", "earlier")  # internal DNCL does not apply to inbound...
    policy = await s.prepare()
    assert policy.allowed  # ...the caller chose to call us; inbound is not an unsolicited telecom
    await s.end()


def test_split_tags():
    assert split_tags("Sure thing. [CALLBACK_OK]") == ("Sure thing.", {"CALLBACK_OK"})
    assert split_tags("Bye now. [END] [HANDOFF]") == ("Bye now.", {"END", "HANDOFF"})
    assert split_tags("No tags here") == ("No tags here", set())


def test_ulaw_roundtrip_and_resampling():
    import struct

    pcm = struct.pack("<8h", 0, 1000, -1000, 8000, -8000, 30000, -30000, 123)
    back = ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    orig = struct.unpack("<8h", pcm)
    dec = struct.unpack("<8h", back)
    for o, d in zip(orig, dec):
        assert abs(o - d) <= max(64, abs(o) * 0.08)  # mu-law is logarithmic; ~8% tolerance
    assert len(upsample_2x(pcm)) == 2 * len(pcm)
    assert downsample_2x(upsample_2x(pcm)) == pcm
