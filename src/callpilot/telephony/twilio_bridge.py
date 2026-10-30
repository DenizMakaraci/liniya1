"""Twilio Programmable Voice bridge for inbound calls (untested against live Twilio; follows the
Media Streams protocol: https://www.twilio.com/docs/voice/media-streams/websocket-messages).

  POST /telephony/twilio/voice     Twilio webhook -> TwiML that opens a bidirectional media stream
  WS   /telephony/twilio/media     mu-law 8 kHz frames <-> InboundSession (PCM16 16 kHz)

Handoff: when the session emits `handoff`, we end the stream; Twilio then continues with the
<Dial> after <Connect>, which rings the human. Needs a Riva server for real ASR/TTS.
"""

import asyncio
import base64
import json
import struct
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..config import settings
from ..knowledge import load_profile

router = APIRouter(prefix="/telephony/twilio", tags=["telephony"])


@router.post("/voice")
async def voice(request: Request):
    form = parse_qs((await request.body()).decode())
    From, CallSid = form.get("From", [""])[0], form.get("CallSid", [""])[0]
    host = request.headers.get("host", "localhost:8000")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/telephony/twilio/media">
      <Parameter name="caller" value="{From}"/>
      <Parameter name="call_sid" value="{CallSid}"/>
    </Stream>
  </Connect>
  <Say>Connecting you to a colleague now.</Say>
  <Dial>{load_profile().handoff_number}</Dial>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/media")
async def media(ws: WebSocket):
    from ..call import InboundSession, make_asr, make_tts
    from ..compliance import CallContext
    from ..compliance.engine import make_engine
    from ..server import client, memory, reasoner, rulebook

    await ws.accept()
    stream_sid, session, pump = None, None, None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            ev = msg.get("event")
            if ev == "start":
                stream_sid = msg["start"]["streamSid"]
                caller = msg["start"].get("customParameters", {}).get("caller", "")
                ctx = CallContext(direction="inbound", mode="autonomous", prospect_number=caller)
                session = InboundSession(ctx, make_engine(settings, rulebook), reasoner, make_asr(),
                                         make_tts(), memory, client)
                pump = asyncio.create_task(_pump_to_twilio(ws, session, stream_sid))
                await session.prepare()
                await session.start()
            elif ev == "media" and session:
                pcm8k = ulaw_to_pcm16(base64.b64decode(msg["media"]["payload"]))
                await session.feed(upsample_2x(pcm8k))
            elif ev == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            await session.end("caller_hung_up")
        if pump:
            pump.cancel()


async def _pump_to_twilio(ws: WebSocket, session, stream_sid: str):
    async for ev in session.stream():
        if ev.kind == "audio":
            pcm8k = downsample_2x(ev.data) if session.tts.sample_rate == 16000 else ev.data
            await ws.send_json({"event": "media", "streamSid": stream_sid,
                                "media": {"payload": base64.b64encode(pcm16_to_ulaw(pcm8k)).decode()}})
        elif ev.kind == "speak":
            await ws.send_json({"event": "mark", "streamSid": stream_sid, "mark": {"name": "speak"}})
        elif ev.kind in ("handoff", "ended"):
            await ws.send_json({"event": "clear", "streamSid": stream_sid})
            await ws.close()
            return


# -- G.711 mu-law <-> PCM16 (Python 3.13 removed audioop) ------------------------------------

BIAS, CLIP = 0x84, 32635


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    out = bytearray()
    for (s,) in struct.iter_unpack("<h", pcm):
        sign = 0x80 if s < 0 else 0
        s = min(abs(s), CLIP) + BIAS
        exp = 7
        mask = 0x4000
        while exp > 0 and not s & mask:
            exp -= 1
            mask >>= 1
        mant = (s >> (exp + 3)) & 0x0F
        out.append(~(sign | (exp << 4) | mant) & 0xFF)
    return bytes(out)


def ulaw_to_pcm16(ulaw: bytes) -> bytes:
    out = bytearray()
    for b in ulaw:
        b = ~b & 0xFF
        sign, exp, mant = b & 0x80, (b >> 4) & 0x07, b & 0x0F
        s = ((mant << 3) + BIAS) << exp
        s -= BIAS
        out += struct.pack("<h", -s if sign else s)
    return bytes(out)


def upsample_2x(pcm: bytes) -> bytes:
    samples = [s for (s,) in struct.iter_unpack("<h", pcm)]
    out = []
    for i, s in enumerate(samples):
        nxt = samples[i + 1] if i + 1 < len(samples) else s
        out += [s, (s + nxt) // 2]
    return struct.pack(f"<{len(out)}h", *out)


def downsample_2x(pcm: bytes) -> bytes:
    samples = [s for (s,) in struct.iter_unpack("<h", pcm)]
    return struct.pack(f"<{len(samples) // 2}h", *samples[::2])
