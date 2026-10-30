from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from callpilot.compliance import (
    CallContext,
    Guardrail,
    PolicyEngine,
    detect_jurisdiction,
    load_rulebook,
)
from callpilot.compliance.models import ConsentEvidence
from callpilot.config import ROOT
from callpilot.llm.reasoner import LegalReasoner

TORONTO = "+14165550123"
MONTREAL = "+15145550123"
NEW_YORK = "+12125550123"
BERLIN = "+493012345678"
COMPANY = {"company": "Acme", "address": "1 Main St", "phone": "+1 800 555 0100",
           "dncl_registered": True, "rep": "Dana", "product": "Scheduler"}


@pytest.fixture(scope="module")
def rulebook():
    return load_rulebook(ROOT / "rules")


@pytest.fixture
def engine(rulebook):
    return PolicyEngine(rulebook, COMPANY)


def weekday_noon(tz):
    d = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo(tz))  # a Wednesday
    assert d.weekday() == 2
    return d


def ctx(number, **kw):
    base = dict(direction="outbound", mode="human", prospect_number=number, audience="consumer",
                local_time=weekday_noon("America/Toronto"), dncl_scrubbed_at=datetime.now())
    base.update(kw)
    return CallContext(**base)


# -- jurisdiction ---------------------------------------------------------------------------

def test_detect_quebec_vs_ontario():
    assert detect_jurisdiction(MONTREAL).rule_code == "CA-QC"
    assert detect_jurisdiction(TORONTO).rule_code == "CA"
    assert detect_jurisdiction(NEW_YORK).rule_code == "US"
    assert detect_jurisdiction(BERLIN).rule_code == "EU"


def test_quebec_inherits_federal(rulebook):
    ids = {r.id for r in rulebook.for_jurisdiction("CA-QC")}
    assert {"qc.law25.purpose_notice", "ca.adad.consent", "default.recording_notice"} <= ids


# -- the core legal question: AI dialing consumers ------------------------------------------

def test_autonomous_outbound_consumer_canada_is_blocked(engine):
    p = engine.evaluate(ctx(TORONTO, mode="autonomous"))
    assert not p.allowed
    assert any("ca.adad.consent" in b for b in p.blocking_reasons)


def test_autonomous_outbound_allowed_with_callback_consent(engine):
    consent = ConsentEvidence(kind="callback_request", number=TORONTO, captured_at=datetime.now(),
                              source="web form 42")
    p = engine.evaluate(ctx(TORONTO, mode="autonomous", consent=consent))
    assert p.allowed
    assert "log_consent_evidence" in p.obligations
    assert p.opening_script[0].startswith("This is an automated call on behalf of Acme")
    assert any("AI assistant" in s for s in p.opening_script)


def test_consent_for_wrong_number_does_not_count(engine):
    consent = ConsentEvidence(kind="written", number=MONTREAL, captured_at=datetime.now(),
                              for_automated_calls=True)
    assert not engine.evaluate(ctx(TORONTO, mode="autonomous", consent=consent)).allowed


def test_dncl_consent_without_adad_consent_is_not_enough(engine):
    consent = ConsentEvidence(kind="written", number=TORONTO, captured_at=datetime.now(),
                              for_automated_calls=False)
    assert not engine.evaluate(ctx(TORONTO, mode="autonomous", consent=consent)).allowed


def test_inbound_autonomous_is_allowed_but_must_disclose(engine):
    p = engine.evaluate(ctx(TORONTO, direction="inbound", mode="autonomous"))
    assert p.allowed
    assert {"ai_disclosure", "recording_notice", "identify_caller_full"} <= p.obligations
    assert "dncl_scrub_31d" not in p.obligations


def test_us_ai_voice_blocked_without_written_consent(engine):
    p = engine.evaluate(ctx(NEW_YORK, mode="autonomous", local_time=weekday_noon("America/New_York")))
    assert any("us.tcpa.ai_voice" in b for b in p.blocking_reasons)


def test_eu_autonomous_blocked(engine):
    p = engine.evaluate(ctx(BERLIN, mode="autonomous", local_time=weekday_noon("Europe/Berlin")))
    assert any("eu.eprivacy.automated" in b for b in p.blocking_reasons)


# -- human copilot path ---------------------------------------------------------------------

def test_human_copilot_canada_allowed_with_script(engine):
    p = engine.evaluate(ctx(TORONTO))
    assert p.allowed, p.blocking_reasons
    assert p.opening_script[0] == "Hi, this is Dana calling from Acme."
    assert any("recorded" in s for s in p.opening_script)
    assert "ai_disclosure" not in p.obligations  # human speaks, no AI disclosure required


def test_quebec_adds_purpose_notice(engine):
    p = engine.evaluate(ctx(MONTREAL))
    assert "purpose_notice" in p.obligations
    assert "french_available" in p.obligations


def test_outside_hours_blocked(engine):
    late = datetime(2026, 10, 28, 22, 0, tzinfo=ZoneInfo("America/Toronto"))
    p = engine.evaluate(ctx(TORONTO, local_time=late))
    assert any("ca.hours" in b for b in p.blocking_reasons)


def test_weekend_hours(engine):
    sat_9am = datetime(2026, 10, 31, 9, 0, tzinfo=ZoneInfo("America/Toronto"))
    assert any("ca.hours" in b for b in engine.evaluate(ctx(TORONTO, local_time=sat_9am)).blocking_reasons)
    sat_11am = sat_9am.replace(hour=11)
    assert engine.evaluate(ctx(TORONTO, local_time=sat_11am)).allowed


def test_stale_dncl_scrub_blocked(engine):
    p = engine.evaluate(ctx(TORONTO, dncl_scrubbed_at=datetime.now() - timedelta(days=40)))
    assert any("31 days" in b for b in p.blocking_reasons)


def test_business_audience_skips_dncl(engine):
    p = engine.evaluate(ctx(TORONTO, audience="business", dncl_scrubbed_at=None))
    assert p.allowed
    assert "dncl_scrub_31d" not in p.obligations


def test_unregistered_company_blocked(rulebook):
    eng = PolicyEngine(rulebook, {**COMPANY, "dncl_registered": False})
    assert any("ca.dncl.register" in b for b in eng.evaluate(ctx(TORONTO)).blocking_reasons)


def test_b2b_autonomous_surfaces_open_question(engine):
    p = engine.evaluate(ctx(TORONTO, mode="autonomous", audience="business"))
    assert any("ca.adad.b2b_unclear" in q for q in p.open_questions)


def test_regulated_sector_adds_disclaimer(engine):
    p = engine.evaluate(ctx(TORONTO, sector="finance"))
    assert "no_regulated_advice" in p.obligations
    assert any("licensed advisor" in s for s in p.opening_script)


def test_applied_rules_have_citations(engine):
    p = engine.evaluate(ctx(TORONTO))
    adad = next(r for r in p.applied_rules if r.id == "ca.hours")
    assert adad.source_url.startswith("https://www.crtc.gc.ca")


# -- guardrail -----------------------------------------------------------------------------

@pytest.fixture
def guardrail(engine):
    claims = Guardrail.load_claims(ROOT / "knowledge" / "facts.md")
    return Guardrail(engine.evaluate(ctx(TORONTO)), claims)


def test_guardrail_blocks_guarantees(guardrail):
    r = guardrail.check_sync("We guarantee you'll save money, it's risk-free.")
    assert not r.allowed and "hard_block:guarantee" in r.violations


async def test_guardrail_offline_grounding(guardrail):
    ok = await guardrail.check("Plans start at $49 per month with a 14-day free trial.")
    assert ok.allowed
    bad = await guardrail.check("It's only $9 per month and cuts no-shows by 80%.")
    assert not bad.allowed and "ungrounded_claim" in bad.violations


def test_guardrail_detects_optout(guardrail):
    assert guardrail.prospect_opted_out("Please don't call this number again.")
    assert guardrail.prospect_opted_out("take me off your list")
    assert not guardrail.prospect_opted_out("Can you call me back tomorrow?")


def test_regulated_advice_blocked_in_finance(engine):
    g = Guardrail(engine.evaluate(ctx(TORONTO, sector="finance")), [], sector="finance")
    assert not g.check_sync("You should invest in this fund, it has a guaranteed return.").allowed


# -- reasoner (offline) ----------------------------------------------------------------------

async def test_reasoner_retrieves_and_cites(rulebook):
    r = LegalReasoner(rulebook, client=None)
    ans = await r.ask("Can an AI voice call consumers in Canada without consent?", "CA")
    ids = {c.rule_id for c in ans.citations}
    assert "ca.adad.consent" in ids
    assert "us.tcpa.ai_voice" not in ids  # filtered to jurisdiction
    assert "not legal advice" in ans.answer.lower()
