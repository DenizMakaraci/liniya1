"""FastAPI app.

REST
  POST /compliance/check   pre-call gate: CallContext -> CallPolicy (+ plain-language explanation)
  POST /compliance/ask     free-form legal question with citations
  GET  /compliance/rules   the rule KB (for the UI / Hermes)
  GET  /audit/{call_id}    compliance log for a call
  POST /memory/forget      right to erasure
WS
  /ws/call                 live copilot: JSON control + text/binary audio frames in, events out
"""

import json

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .call import CopilotSession, ProspectMemory, make_asr
from .compliance import AuditLog, CallContext, load_rulebook
from .compliance.engine import make_engine
from .config import settings
from .llm import LegalReasoner, get_client

app = FastAPI(title="CallPilot", version="0.1.0")
rulebook = load_rulebook(settings.rules_dir)
memory = ProspectMemory(settings.data_dir)
client = get_client()
reasoner = LegalReasoner(rulebook, client)


class CheckRequest(BaseModel):
    ctx: CallContext
    rep: str = ""
    product: str = ""
    explain: bool = True


class AskRequest(BaseModel):
    question: str
    jurisdiction: str | None = None


@app.post("/compliance/check")
async def compliance_check(req: CheckRequest):
    engine = make_engine(settings, rulebook, req.rep, req.product)
    policy = engine.evaluate(req.ctx)
    out = {"policy": policy.model_dump(mode="json")}
    if req.explain:
        out["explanation"] = (await reasoner.explain_policy(policy)).model_dump()
    return out


@app.post("/compliance/ask")
async def compliance_ask(req: AskRequest):
    return (await reasoner.ask(req.question, req.jurisdiction)).model_dump()


@app.get("/compliance/rules")
def compliance_rules(jurisdiction: str | None = None):
    rules = rulebook.for_jurisdiction(jurisdiction) if jurisdiction else rulebook.all_rules()
    return {"sources": {k: v.model_dump() for k, v in rulebook.sources.items()},
            "rules": [r.model_dump() for r in rules]}


@app.get("/audit/{call_id}")
def audit(call_id: str):
    log = AuditLog(settings.data_dir, call_id)
    if not log.path.exists():
        raise HTTPException(404, "unknown call")
    return log.read()


@app.post("/memory/forget")
def forget(number: str):
    memory.forget(number)
    return {"ok": True}


@app.websocket("/ws/call")
async def ws_call(ws: WebSocket):
    """Protocol:
    client -> {"type":"start","ctx":{...CallContext},"rep":"Dana","product":"Acme Scheduler"}
    client -> {"type":"text","speaker":"prospect","text":"..."}    (mock ASR / typed transcript)
    client -> binary frame prefixed by 1 byte: 0=rep, 1=prospect, then PCM16 16kHz   (Riva)
    client -> {"type":"end","outcome":"booked_demo"}
    server -> {"kind": ..., "data": ...} SessionEvents
    """
    await ws.accept()
    msg = json.loads(await ws.receive_text())
    if msg.get("type") != "start":
        await ws.close(code=1003)
        return
    ctx = CallContext.model_validate(msg["ctx"])
    engine = make_engine(settings, rulebook, msg.get("rep", ""), msg.get("product", ""))
    session = CopilotSession(ctx, engine, reasoner, make_asr(), memory, client, msg.get("rep", "the rep"))

    async def pump():
        async for ev in session.stream():
            data = ev.data.model_dump(mode="json") if isinstance(ev.data, BaseModel) else ev.data
            if hasattr(data, "__dataclass_fields__"):
                data = data.__dict__
            await ws.send_json({"kind": ev.kind, "data": data})

    import asyncio

    pump_task = asyncio.create_task(pump())
    try:
        policy = await session.prepare()
        await session.start()
        if not policy.allowed:
            await pump_task
            await ws.close()
            return
        while True:
            frame = await ws.receive()
            if frame.get("text") is not None:
                m = json.loads(frame["text"])
                if m["type"] == "text":
                    await session.feed(m["speaker"], m["text"].encode())
                elif m["type"] == "end":
                    await session.end(m.get("outcome", "completed"))
                    break
            elif frame.get("bytes"):
                b = frame["bytes"]
                await session.feed("prospect" if b[0] else "rep", b[1:])
        await pump_task
    except WebSocketDisconnect:
        await session.end("disconnected")
        pump_task.cancel()
