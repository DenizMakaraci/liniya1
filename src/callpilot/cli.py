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
        ("rep", f"Hi, this is {a.rep} calling from {settings.company_name}. This call is recorded for quality."),
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

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")

    a = p.parse_args(argv)
    if a.cmd == "serve":
        return cmd_serve(a)
    return asyncio.run({"check": cmd_check, "ask": cmd_ask, "demo": cmd_demo}[a.cmd](a))


if __name__ == "__main__":
    sys.exit(main())
