"""Tool definitions (OpenAI function-calling schema) so a Hermes Agent / OpenClaw running inside
NemoClaw can use CallPilot as skills: check compliance before dialing, ask legal questions, read
and write prospect memory, and pull the audit trail. `dispatch()` executes a call by name.

Mount as an MCP server or import directly; every tool is local, no network egress needed.
"""

from datetime import datetime

from ..call.memory import ProspectMemory
from ..compliance import CallContext, load_rulebook
from ..compliance.audit import AuditLog
from ..compliance.engine import make_engine
from ..config import settings
from ..llm import LegalReasoner, get_client

TOOLS = [
    {"type": "function", "function": {
        "name": "compliance_check_call",
        "description": "Decide whether a sales call to a phone number is lawful and what must be said first. "
                       "Always call this before dialing.",
        "parameters": {"type": "object", "properties": {
            "number": {"type": "string", "description": "E.164 phone number"},
            "mode": {"type": "string", "enum": ["human", "autonomous"]},
            "direction": {"type": "string", "enum": ["outbound", "inbound"], "default": "outbound"},
            "audience": {"type": "string", "enum": ["consumer", "business", "unknown"], "default": "unknown"},
            "sector": {"type": "string", "default": "general"},
            "consent_kind": {"type": "string", "enum": ["written", "oral", "electronic", "callback_request"]},
            "dncl_scrubbed": {"type": "boolean", "default": False},
        }, "required": ["number", "mode"]}}},
    {"type": "function", "function": {
        "name": "compliance_ask",
        "description": "Ask a telemarketing/privacy compliance question; returns an answer with rule citations.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "jurisdiction": {"type": "string", "description": "e.g. CA, CA-QC, US, EU"},
        }, "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "memory_get_prospect",
        "description": "Read what we know about a prospect (profile + past interactions).",
        "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}}},
    {"type": "function", "function": {
        "name": "memory_update_prospect",
        "description": "Store or update prospect profile fields.",
        "parameters": {"type": "object", "properties": {
            "number": {"type": "string"}, "name": {"type": "string"}, "company": {"type": "string"},
            "audience": {"type": "string"}, "sector": {"type": "string"}, "notes": {"type": "string"},
        }, "required": ["number"]}}},
    {"type": "function", "function": {
        "name": "memory_forget_prospect",
        "description": "Erase all data about a prospect (right to erasure / opt-out).",
        "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}}},
    {"type": "function", "function": {
        "name": "audit_get_call",
        "description": "Return the compliance audit log of a call.",
        "parameters": {"type": "object", "properties": {"call_id": {"type": "string"}}, "required": ["call_id"]}}},
]


async def dispatch(name: str, args: dict) -> dict:
    rb = load_rulebook(settings.rules_dir)
    mem = ProspectMemory(settings.data_dir)
    if name == "compliance_check_call":
        from ..compliance.models import ConsentEvidence

        consent = None
        if args.get("consent_kind"):
            consent = ConsentEvidence(kind=args["consent_kind"], number=args["number"],
                                      captured_at=datetime.now(), source="agent",
                                      for_automated_calls=args.get("mode") == "autonomous")
        ctx = CallContext(direction=args.get("direction", "outbound"), mode=args["mode"],
                          prospect_number=args["number"], audience=args.get("audience", "unknown"),
                          sector=args.get("sector", "general"), consent=consent,
                          dncl_scrubbed_at=datetime.now() if args.get("dncl_scrubbed") else None)
        policy = make_engine(settings, rb).evaluate(ctx)
        legal = await LegalReasoner(rb, get_client()).explain_policy(policy)
        return {"policy": policy.model_dump(mode="json"), "explanation": legal.answer,
                "citations": [c.model_dump() for c in legal.citations]}
    if name == "compliance_ask":
        return (await LegalReasoner(rb, get_client()).ask(args["question"], args.get("jurisdiction"))).model_dump()
    if name == "memory_get_prospect":
        return {"profile": mem.get(args["number"]), "history": mem.history(args["number"])}
    if name == "memory_update_prospect":
        number = args.pop("number")
        mem.upsert(number, **{k: v for k, v in args.items() if v is not None})
        return {"ok": True}
    if name == "memory_forget_prospect":
        mem.forget(args["number"])
        return {"ok": True}
    if name == "audit_get_call":
        return {"events": AuditLog(settings.data_dir, args["call_id"]).read()}
    raise ValueError(f"unknown tool {name}")
