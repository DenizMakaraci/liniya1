from pathlib import Path

from callpilot.call.memory import ProspectMemory
from callpilot.compliance.audit import AuditLog
from callpilot.config import ROOT
from callpilot.knowledge import import_contacts, import_suppression, load_kb, load_profile
from callpilot.knowledge.kb import chunk_markdown, parse_facts
from callpilot.knowledge.profile import CompanyProfile

# -- profile ------------------------------------------------------------------------------------

def test_profile_from_yaml(tmp_path):
    (tmp_path / "company.yaml").write_text("name: Foo Ltd\ncallback_number: '+1 800 1'\nassistant_name: Max\n")
    p = load_profile(tmp_path / "company.yaml")
    assert p.name == "Foo Ltd" and p.assistant_name == "Max"
    assert p.handoff_number == p.callback_number  # defaults
    assert p.disclosure_vars(rep="Dana")["company"] == "Foo Ltd"


def test_profile_missing_file_uses_env_defaults(tmp_path):
    p = load_profile(tmp_path / "nope.yaml")
    assert isinstance(p, CompanyProfile) and p.name


# -- knowledge base ---------------------------------------------------------------------------

def test_parse_facts_strips_markdown():
    facts = parse_facts("# Approved\n<!-- note -->\n- Plans start at $49.\n2. Trial is 14 days.\n\n")
    assert facts == ["Plans start at $49.", "Trial is 14 days."]


def test_chunk_markdown_by_heading():
    chunks = chunk_markdown("faq", "# A\nfirst\n\n## B\nsecond para\n\nthird para")
    assert [(c.heading, c.text) for c in chunks] == [("A", "first"), ("B", "second para\n\nthird para")]


def test_repo_kb_loads_and_retrieves():
    kb = load_kb(ROOT / "knowledge")
    assert kb.stats["facts"] >= 4 and kb.stats["docs"] >= 2
    hits = kb.retrieve("how do I change my billing details")
    assert hits and hits[0].doc == "billing faq" and "Billing" in hits[0].text
    assert "docs" not in {c.doc for c in kb.chunks} and "README" not in {c.doc.lower() for c in kb.chunks}


def test_kb_context_respects_budget(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "big.md").write_text("# T\n" + ("word " * 400 + "\n\n") * 5)
    kb = load_kb(tmp_path)
    assert len(kb.context_for("word", k=5, max_chars=900)) <= 900


# -- importers --------------------------------------------------------------------------------

def test_import_contacts_validates_consent(tmp_path):
    mem = ProspectMemory(tmp_path)
    rep = import_contacts(Path(ROOT / "examples" / "contacts.csv"), mem)
    assert rep.imported == 4
    assert rep.consents == 2  # Jordan (callback) and Alex (electronic)
    reasons = {n: why for n, why in rep.rejected}
    assert any("without consent_date and consent_source" in w for w in reasons.values())  # Sam: no source
    assert any("invalid number" in w for w in reasons.values())  # 555-0100
    jordan = mem.get("+16045550142")
    assert jordan["audience"] == "business" and "callback_request" in jordan["consent_json"]
    assert mem.get("+15145550123")["notes"] == "Prefers French"


def test_import_contacts_rejects_consent_for_other_number(tmp_path):
    csv = ("number,consent_kind,consent_date,consent_source,consent_number\n"
           "+14165550123,written,2026-01-01,form,+14165559999\n")
    rep = import_contacts(csv, ProspectMemory(tmp_path))
    assert rep.imported == 0 and "consent_number does not match" in rep.rejected[0][1]


def test_import_contacts_requires_number_column(tmp_path):
    rep = import_contacts("name,phone\nA,+14165550123\n", ProspectMemory(tmp_path))
    assert rep.rejected == [(0, "missing required 'number' column")]


def test_import_suppression_blocks_outbound(tmp_path):
    audit = AuditLog(tmp_path, "_import")
    rep = import_suppression(Path(ROOT / "examples" / "suppression.txt"), audit)
    assert rep.imported == 2 and not rep.rejected
    assert audit.is_internally_blocked("+14165550177")
    # idempotent
    import_suppression(Path(ROOT / "examples" / "suppression.txt"), audit)
    assert audit.internal_dncl.read_text().count("+14165550177") == 1
