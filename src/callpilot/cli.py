"""CLI: `callpilot check +15145550123 --mode autonomous`, `callpilot ask "..."`, `callpilot serve`,
`callpilot demo` (scripted call through the full pipeline using the mock ASR)."""

import argparse
import asyncio
import json
import sys
from datetime import datetime

from .compliance import CallContext, load_rulebook
from .compliance.engine import make_engine
from .compliance.models import ConsentEvidence
from .config import settings
from .knowledge import load_profile
from .llm import LegalReasoner, get_client


def _ctx(a) -> CallContext:
    consent = None
    if a.consent:
        consent = ConsentEvidence(kind=a.consent, number=a.number, captured_at=datetime.now(),
                                  source="cli", for_automated_calls=a.mode == "autonomous")
    return CallContext(direction=a.direction, mode=a.mode, prospect_number=a.number,
                       audience=a.audience, sector=a.sector, consent=consent,
                       local_time=datetime.fromisoformat(a.at) if a.at else None,
                       dncl_scrubbed_at=datetime.now() if a.scrubbed else None)


def _print_policy(p, legal=None):
    print(f"Jurisdiction : {p.jurisdiction} ({p.jurisdiction_description})")
    print(f"Decision     : {'ALLOWED' if p.allowed else 'BLOCKED'}")
    for b in p.blocking_reasons:
        print(f"  blocked by : {b}")
    if p.calling_window:
        print(f"Window       : {p.calling_window}")
    if p.opening_script:
        print("Must say first:")
        for s in p.opening_script:
            print(f"  - {s}")
    if p.obligations:
        print(f"Obligations  : {', '.join(sorted(p.obligations))}")
    for w in p.warnings:
        print(f"  note       : {w}")
    for q in p.open_questions:
        print(f"  UNSETTLED  : {q}")
    print("Applied rules:")
    for r in p.applied_rules:
        print(f"  [{r.id}] {r.effect:8} {r.source_title} {r.section}")
    if legal:
        print("\n--- Legal reasoning ---\n" + legal.answer)


async def cmd_check(a):
    rb = load_rulebook(settings.rules_dir)
    policy = make_engine(settings, rb, a.rep, a.product).evaluate(_ctx(a))
    legal = await LegalReasoner(rb, get_client()).explain_policy(policy) if a.explain else None
    if a.json:
        print(json.dumps({"policy": policy.model_dump(mode="json"),
                          "legal": legal.model_dump() if legal else None}, indent=2))
    else:
        _print_policy(policy, legal)
    return 0 if policy.allowed else 2


async def cmd_ask(a):
    rb = load_rulebook(settings.rules_dir)
    ans = await LegalReasoner(rb, get_client()).ask(a.question, a.jurisdiction)
    print(ans.answer)
    print("\nCitations:")
    for c in ans.citations:
        print(f"  [{c.rule_id}] {c.source_title} {c.section} {c.source_url}")
    return 0


async def cmd_demo(a):
    from .call import CopilotSession, MockASR, ProspectMemory

    rb = load_rulebook(settings.rules_dir)
    session = CopilotSession(_ctx(a), make_engine(settings, rb, a.rep, a.product),
                             LegalReasoner(rb, get_client()), MockASR(), ProspectMemory(settings.data_dir),
                             get_client(), a.rep)
    script = [
        ("rep", f"Hi, this is {a.rep} calling from {load_profile().name}. This call is recorded for quality."),
        ("prospect", "Okay, what is this about?"),
        ("rep", "We help clinics cut missed appointments. Do you handle scheduling?"),
        ("prospect", "We do, but we already use a tool. How much cheaper are you?"),
        ("prospect", "Actually, please don't call this number again."),
    ]

    async def drive():
        policy = await session.prepare()
        await session.start()
        if not policy.allowed:
            return
        for speaker, text in script:
            await session.feed(speaker, text.encode())
            await asyncio.sleep(0.8)
        await session.end("prospect_opted_out")

    asyncio.create_task(drive())
    async for ev in session.stream():
        if ev.kind == "policy":
            _print_policy(ev.data)
        elif ev.kind == "legal":
            print("\n--- Legal reasoning ---\n" + ev.data.answer + "\n")
        elif ev.kind == "brief":
            print("--- Brief ---\n" + ev.data + "\n")
        elif ev.kind == "transcript":
            print(f"  {ev.data.speaker:8}> {ev.data.text}")
        elif ev.kind in ("whisper", "opt_out"):
            tag = "MANDATORY" if ev.data.get("mandatory") else "whisper"
            print(f"  [{tag}] {ev.data['text']}")
            if ev.data.get("violations"):
                print(f"           guardrail: {ev.data['violations']}")
        elif ev.kind == "blocked":
            print("Call blocked:", ev.data)
        elif ev.kind == "ended":
            print(f"\n--- Ended --- audit: data/audit/{ev.data['audit']}\n{ev.data['summary']}")
    return 0


async def cmd_inbound_demo(a):
    from .call import InboundSession, MockASR, MockTTS, ProspectMemory

    rb = load_rulebook(settings.rules_dir)
    ctx = CallContext(direction="inbound", mode="autonomous", prospect_number=a.number,
                      audience=a.audience, sector=a.sector)
    session = InboundSession(ctx, make_engine(settings, rb), LegalReasoner(rb, get_client()), MockASR(),
                             MockTTS(), ProspectMemory(settings.data_dir), get_client(), silence_timeout=30)
    scripts = {
        "callback": ["Hi, I saw your ad about the scheduler. Does it work with Outlook?",
                     "What does it cost?", "Is it true it cuts no-shows by 80 percent?",
                     "Okay, can someone call me back tomorrow to set it up?", "Yes please, this number is fine.",
                     "Thanks, bye."],
        "human": ["Hi, I need to change my billing details.", "Can I speak to a real person please?"],
        "optout": ["Who is this?", "Please remove my number and don't contact me again."],
    }

    async def drive():
        policy = await session.prepare()
        await session.start()
        if not policy.allowed:
            return
        for line in scripts[a.script]:
            if session.outcome:
                break
            await asyncio.sleep(1.0)
            await session.feed(line.encode())
            await asyncio.sleep(2.5 if get_client() else 0.3)
        if not session.outcome:
            await session.end("caller_hung_up")

    asyncio.create_task(drive())
    async for ev in session.stream():
        if ev.kind == "policy":
            _print_policy(ev.data)
            print()
        elif ev.kind == "brief":
            print("--- Brief ---\n" + ev.data + "\n")
        elif ev.kind == "transcript":
            print(f"  caller > {ev.data.text}")
        elif ev.kind == "speak":
            tag = "AI (mandatory)" if ev.data.get("mandatory") else "AI"
            print(f"  {tag:14}> {ev.data['text']}")
            if ev.data.get("violations"):
                print(f"                 guardrail: {ev.data['violations']}")
        elif ev.kind in ("opt_out", "handoff"):
            print(f"  ** {ev.kind.upper()}: {ev.data.get('reason') or ev.data.get('text')}")
        elif ev.kind == "blocked":
            print("Call blocked:", ev.data)
        elif ev.kind == "ended":
            print(f"\n--- Ended ({ev.data['outcome']}) --- audit: data/audit/{ev.data['audit']}")
            if ev.data["consents"]:
                print("Consent captured:", ev.data["consents"][0]["source"])
            print(ev.data["summary"])
    return 0


def cmd_import(a):
    from pathlib import Path

    from .call import ProspectMemory
    from .compliance.audit import AuditLog
    from .knowledge import import_contacts, import_suppression

    if a.what == "contacts":
        rep = import_contacts(Path(a.path), ProspectMemory(settings.data_dir), a.region)
    else:
        rep = import_suppression(Path(a.path), AuditLog(settings.data_dir, "_import"), a.region)
    print(rep.summary())
    return 0 if not rep.rejected else 1


def cmd_knowledge(a):
    from .knowledge import load_kb, load_profile

    p, kb = load_profile(), load_kb()
    print("Company profile:", settings.company_file, "(exists)" if settings.company_file.exists() else "(missing, using env)")
    print(p.as_prompt())
    print(f"\nKnowledge: {kb.stats}  ({settings.knowledge_dir})")
    for f in kb.facts:
        print(f"  fact: {f}")
    if a.query:
        print(f"\nRetrieval for {a.query!r}:")
        for c in kb.retrieve(a.query):
            print("  " + c.render().replace("\n", "\n  ")[:400])
    return 0


def cmd_forget(a):
    from .call import ProspectMemory

    ProspectMemory(settings.data_dir).forget(a.number)
    print(f"erased {a.number} from memory (audit logs are retained as required by law)")
    return 0


def cmd_serve(a):
    import uvicorn

    uvicorn.run("callpilot.server:app", host=a.host, port=a.port, reload=a.reload)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="callpilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    def call_args(sp):
        sp.add_argument("number")
        sp.add_argument("--direction", default="outbound", choices=["outbound", "inbound"])
        sp.add_argument("--mode", default="human", choices=["human", "autonomous"])
        sp.add_argument("--audience", default="unknown", choices=["consumer", "business", "unknown"])
        sp.add_argument("--sector", default="general")
        sp.add_argument("--consent", choices=["written", "oral", "electronic", "callback_request"])
        sp.add_argument("--scrubbed", action="store_true", help="number was DNCL-scrubbed today")
        sp.add_argument("--at", help="simulate prospect local time, ISO e.g. 2026-10-28T14:00-04:00")
        sp.add_argument("--rep", default="Dana")
        sp.add_argument("--product", default="Acme Scheduler")

    c = sub.add_parser("check", help="pre-call jurisdiction gate")
    call_args(c)
    c.add_argument("--no-explain", dest="explain", action="store_false")
    c.add_argument("--json", action="store_true")

    q = sub.add_parser("ask", help="ask a compliance question with citations")
    q.add_argument("question")
    q.add_argument("--jurisdiction")

    d = sub.add_parser("demo", help="scripted end-to-end call with mock ASR")
    call_args(d)

    i = sub.add_parser("inbound-demo", help="AI answers a scripted inbound call (mock ASR/TTS)")
    i.add_argument("number")
    i.add_argument("--audience", default="unknown", choices=["consumer", "business", "unknown"])
    i.add_argument("--sector", default="general")
    i.add_argument("--script", default="callback", choices=["callback", "human", "optout"])

    im = sub.add_parser("import", help="import contacts CSV or a suppression (do-not-call) list")
    im.add_argument("what", choices=["contacts", "suppression"])
    im.add_argument("path")
    im.add_argument("--region", default="CA", help="default region for numbers without country code")

    k = sub.add_parser("knowledge", help="show company profile, approved facts and test retrieval")
    k.add_argument("query", nargs="?")

    f = sub.add_parser("forget", help="erase a prospect from memory (right to erasure)")
    f.add_argument("number")

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")

    a = p.parse_args(argv)
    sync = {"serve": cmd_serve, "import": cmd_import, "knowledge": cmd_knowledge, "forget": cmd_forget}
    if a.cmd in sync:
        return sync[a.cmd](a)
    cmds = {"check": cmd_check, "ask": cmd_ask, "demo": cmd_demo, "inbound-demo": cmd_inbound_demo}
    return asyncio.run(cmds[a.cmd](a))


if __name__ == "__main__":
    sys.exit(main())
