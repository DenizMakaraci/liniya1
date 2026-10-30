# CallPilot

**The phone assistant that knows the law.** A private, always-on AI for business phone calls: it
coaches your reps on outbound calls, answers your inbound line, and refuses to do anything the law
in the caller's jurisdiction forbids - while every byte of customer data stays on your machine.

> **Nebius x NVIDIA Global AI Hackathon - Personal AI Track.** License: MIT.
> Demo video: _add YouTube link_ · Fictional demo data throughout.

## How NVIDIA and Nebius are used

| Requirement | Where |
|---|---|
| **Runtime calls to Nebius Token Factory** | Every LLM call goes through the OpenAI-compatible client in `src/callpilot/llm/client.py` to `https://api.tokenfactory.nebius.com/v1/`. The CLI prints the provider on every run (`[callpilot] inference: Nebius Token Factory`). |
| **NVIDIA open model** | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (Nemotron 3, 30B MoE / 3B active) for *everything*: whispers, inbound replies, guardrail judge, briefs (thinking off) and the legal reasoner (thinking on). Model IDs are env vars; Super/Ultra are one-line upgrades. |
| **NVIDIA NemoClaw / OpenShell** | `nemoclaw/Dockerfile` + `nemoclaw/callpilot-policy.yaml`: sandbox with deny-all egress except Token Factory, API key injected by the gateway, rw only on `data/`. |
| **Hermes Agent** | `src/callpilot/tools/` exposes 8 tools (compliance gate, legal Q&A, memory, knowledge, audit) as OpenAI function schemas and a stdio MCP server, so a Hermes agent must pass the gate before it dials. |
| **NVIDIA speech (Parakeet / Magpie)** | Riva adapters in `src/callpilot/call/asr.py`, `tts.py` (mock used in the demo). |

### Persistent memory · reusable skills · your data, your control
Prospect memory and consent evidence in local SQLite; skills as MCP tools; approved facts, knowledge
docs and the legal rule base as read-only files; audit trail as JSONL; `callpilot forget` for erasure.

## Quick start (judges: ~3 minutes)

```bash
git clone <repo> && cd callpilot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env         # paste your Nebius Token Factory key (promo code NEBIUS-DEVPOST-GLOBAL26)
./scripts/demo-reset.sh      # fictional company, contacts, suppression list
.venv/bin/callpilot models   # confirm Nemotron is reachable on Nebius

.venv/bin/callpilot check +15145550123 --mode autonomous --audience consumer      # BLOCKED, with CRTC citations
.venv/bin/callpilot ask "Can an AI call businesses in Canada without consent?" --jurisdiction CA
.venv/bin/callpilot inbound-demo +16045550142 --script callback                   # AI answers the phone
.venv/bin/callpilot inbound-demo +16045550199 --script optout && .venv/bin/callpilot check +16045550199 --scrubbed
.venv/bin/python -m pytest -q                                                      # 43 tests, no key needed
```

Without a key everything still runs in a labelled offline mode (deterministic gate, rule listing,
canned replies) so the compliance logic can be inspected at zero cost.

> Not legal advice. The rule knowledge base is a starting point written from public regulator
> sources; have counsel review it before production use.

## What "reasoning about the laws" means here

| Layer | When | How | Latency |
|---|---|---|---|
| **Jurisdiction gate** | before dialing | Phone number -> jurisdiction (CA, CA-QC, US, EU) -> deterministic policy engine over a YAML rule KB with citations. Blocks the call (AI dialing consumers without consent, outside hours, stale DNCL scrub, unregistered company) or returns the mandatory opening script and obligations. | <1 ms |
| **Legal explanation / Q&A** | before dialing, on demand | BM25 retrieval over the rule KB -> Nemotron 3 Ultra (reasoning on) answers with `[rule-id]` citations. Works offline without an LLM (lists the rules). | seconds, off the call path |
| **Live guardrail** | every whisper | Regex hard-blocks (guarantees, fake urgency, impersonation, regulated advice) + Nemotron Nano JSON judge grounding every price/statistic in the approved-claims list; rewrites or suppresses. Watches the prospect for opt-out intent. | ~1 ms + ~400 ms when a claim needs judging |
| **Audit log** | always | JSONL per call: rules applied, disclosures actually spoken (detected from the rep's transcript), consent evidence, guardrail interventions, opt-outs (-> internal DNCL), retention date. Memory purges on schedule; `forget` for erasure. | - |

Rule files: `rules/*.yaml`. Each rule is both machine-evaluable (`effect`, conditions, `hours`,
`unless_consent`) and citable (`source`, `section`, `summary`, `text`). Mark anything genuinely
unsettled with `confidence: unclear` and the engine surfaces it as an open question for the
reasoner / a human instead of silently deciding.

## Pipeline

```
 rep mic ─┐                                    ┌─ whisper ─> guardrail ─> rep screen
 prospect ┴─> ASR (Riva/Parakeet, ~20ms) ─> transcript ─┤
                                               └─ every 6 turns: slow path refreshes brief (background)
 before call:  jurisdiction gate -> policy -> legal explanation -> call brief (Nemotron Ultra)
 after call:   summary -> prospect memory (SQLite) ; audit JSONL ; retention purge
```

- One open model, `nvidia/nemotron-3.5-lightning-30b-a3b` (30B MoE, 3B active), does everything:
  thinking **off** for whispers, replies, the guardrail judge, briefs and summaries (~2 s for a brief,
  ~400 ms TTFT), thinking **on** for the legal reasoner only (~7-12 s, off the call path).
- Served via any OpenAI-compatible endpoint: build.nvidia.com (free tier) or Nebius Token Factory
  (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, zero retention, EU). Larger Nemotron (Super/Ultra) is a
  one-line upgrade for the reasoner on a dedicated endpoint; on the free tier Super VL with thinking
  measured ~108 s per brief, so it is not the default.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # add NEBIUS_API_KEY to enable the LLM paths (optional)

# Pre-call gate (exit code 2 = blocked)
.venv/bin/callpilot check +15145550123 --mode autonomous --audience consumer
.venv/bin/callpilot check +14165550123 --mode human --audience business --at 2026-10-28T14:00-04:00
.venv/bin/callpilot check +14165550123 --mode autonomous --consent callback_request

# Ask the reasoner
.venv/bin/callpilot ask "Can our AI answer inbound calls in Quebec?" --jurisdiction CA-QC

# Scripted end-to-end call through the real pipeline (mock ASR)
CALLPILOT_DNCL_REGISTERED=true .venv/bin/callpilot demo +14165550123 --audience business --at 2026-10-28T14:00-04:00
cat data/audit/*.jsonl

# API + WebSocket copilot
.venv/bin/callpilot serve          # http://127.0.0.1:8000/docs

.venv/bin/python -m pytest
```

Set `RIVA_SERVER=host:50051` to switch from the mock ASR to a Riva / Parakeet NIM
(`pip install -e ".[speech]"`).

### WebSocket protocol (`/ws/call`)

```jsonc
-> {"type":"start","ctx":{"direction":"outbound","mode":"human","prospect_number":"+1416...","audience":"business"},"rep":"Dana","product":"Acme Scheduler"}
<- {"kind":"policy",...} {"kind":"legal",...} {"kind":"brief",...}
<- {"kind":"whisper","data":{"text":"Hi, this is Dana calling from Acme Inc.. This call is being recorded...","mandatory":true}}
-> {"type":"text","speaker":"prospect","text":"What's this about?"}        // or binary: [0|1 speaker byte][PCM16 16k]
<- {"kind":"transcript",...} {"kind":"whisper",...}
-> {"type":"end","outcome":"booked_demo"}
<- {"kind":"ended","data":{"summary":"...","audit":"<call_id>.jsonl"}}
```

## Inbound mode: the AI answers the call

Businesses can let the assistant answer incoming calls (overflow, after-hours, lead qualification,
callback booking). Inbound calls are not "unsolicited telecommunications", so the CRTC consent and
DNCL rules don't block them - but the AI must disclose itself, the recording, and (Quebec) the
Law 25 notices, which it speaks itself and audits line by line.

```bash
.venv/bin/callpilot inbound-demo +15145550123 --script callback   # Quebec caller, books a callback
.venv/bin/callpilot inbound-demo +16045550199 --script human      # asks for a person -> handoff
.venv/bin/callpilot inbound-demo +16045550199 --script optout     # opts out -> internal DNCL
.venv/bin/callpilot check +16045550199 --scrubbed                 # outbound now BLOCKED for that number
```

`InboundSession` (`src/callpilot/call/inbound.py`) handles: spoken mandatory opening,
Nemotron Lightning replies (thinking off) through the same guardrail, barge-in (caller speech cancels
in-flight TTS), silence timeout ("are you still there?"), human handoff (on request, regulated
topics, repeated guardrail blocks, or the model's own `[HANDOFF]` tag), opt-out, and **callback
consent capture**: when the AI asks permission to call back and the caller says yes, a
`callback_request` consent record is written to memory and the audit log - which is exactly what the
outbound gate needs to allow an automated callback later.

- WebSocket: `/ws/inbound` (`{"type":"start","caller":"+1514..."}` then text or PCM16 frames).
- Twilio: point a number's voice webhook at `POST /telephony/twilio/voice`; the TwiML opens a
  bidirectional Media Stream to `/telephony/twilio/media` (mu-law 8 kHz <-> PCM16 16 kHz conversion
  included) and falls through to `<Dial>` of your human line on handoff. Untested against live
  Twilio; needs `RIVA_SERVER` for real ASR/TTS (Parakeet + Magpie).

## Onboarding a company's data

There is no upload step: data goes from the company's files into the sandbox volume, and the only
thing that ever leaves is the prompt to the inference endpoint. Five artifacts, all plain files:

| Artifact | Where | Trust level | Used for |
|---|---|---|---|
| Company profile | `company.yaml` | read-only | identity in disclosures, handoff number, languages, DNCL registration, assistant name |
| Approved facts | `knowledge/facts.md` | read-only, legal-reviewed | the only prices/stats/guarantees the AI may **assert**; ground truth for the guardrail judge |
| Knowledge docs | `knowledge/docs/*.md,txt,pdf` | read-only | FAQs/policies the AI may **answer from**; chunked by heading, BM25-retrieved per caller turn |
| Contacts | CSV -> `data/memory.sqlite` | rw | prospect profiles, history, **consent evidence** |
| Suppression list | TXT/CSV -> `data/internal_dncl.txt` | rw | existing do-not-call numbers; blocks all outbound |

```bash
.venv/bin/callpilot knowledge "change billing details"       # profile, facts, retrieval test
.venv/bin/callpilot import contacts examples/contacts.csv     # validates consent evidence
.venv/bin/callpilot import suppression examples/suppression.txt
.venv/bin/callpilot forget +14165550123                        # right to erasure
```

The contacts importer **rejects rows that claim consent without a date and a source** - the CRTC
requires consent to be demonstrable, so undocumented consent is treated as none. Consent is also
per-number: a `consent_number` that differs from `number` is rejected. Same endpoints over REST:
`POST /import/contacts` (CSV body), `POST /import/suppression`, `GET /knowledge?q=...`; and as agent
tools `knowledge_search` / `approved_facts`.

The two knowledge tiers are deliberate: a price that appears only in a doc is still blocked by the
guardrail until someone adds it to `facts.md`. Both folders are read-only inside OpenShell so the
model cannot rewrite its own ground truth.

## Running inside NemoClaw / OpenShell (Hermes Agent)

- `nemoclaw/Dockerfile` - sandbox image; `nemoclaw/callpilot-policy.yaml` - intended OpenShell
  policy: deny-all egress except the Nebius inference route (credential injected by the gateway's
  L7 proxy, the sandbox only ever holds a placeholder), optional telephony and local Riva; rw only
  on `/sandbox/callpilot/data`.
- `python -m callpilot.tools.mcp_server` - MCP stdio server exposing `compliance_check_call`,
  `compliance_ask`, `memory_*`, `audit_get_call` so the Hermes agent must consult the gate before
  it ever dials, and can answer "is this legal?" with citations.
- Onboard with `nemoclaw onboard --from nemoclaw/Dockerfile`, pick the Hermes harness and the Nebius
  provider, then attach the policy. Adjust field names to your OpenShell version.

## Layout

```
rules/                      legal rule KB (default, ca, ca-qc, us, eu)
knowledge/                  facts.md (approved claims) + docs/ (FAQs, policies)
company.yaml                company profile
examples/                   sample contacts.csv / suppression.txt (fictional)
src/callpilot/compliance/   jurisdiction, rules, engine (gate), guardrail, audit
src/callpilot/knowledge/    company profile, knowledge base + retrieval, CSV importers
src/callpilot/llm/          inference client, fast path, speaker, slow path, legal RAG reasoner
src/callpilot/call/         ASR/TTS (mock + Riva), prospect memory, CopilotSession (outbound,
                            human speaks) and InboundSession (AI speaks)
src/callpilot/telephony/    Twilio voice webhook + Media Streams bridge
src/callpilot/tools/        Hermes/OpenAI tool schemas + MCP server
src/callpilot/server.py     FastAPI REST + WebSockets (/ws/call, /ws/inbound)
src/callpilot/cli.py        check / ask / demo / inbound-demo / import / knowledge / forget / serve
nemoclaw/                   Dockerfile + OpenShell policy
tests/                      43 tests: compliance engine, guardrail, reasoner, inbound session, knowledge, importers
```

## Extending the rule base

Add a `rules/<code>.yaml` (e.g. `uk.yaml`), map numbers to it in
`compliance/jurisdiction.py`, and write rules. Every rule needs a `source` with a URL so the
reasoner's citations resolve. Prefer `effect: forbid` + `unless_consent` for consent-gated
activities, `require` + `disclosure` for things that must be said, `restrict` + `hours` for time
windows, and `advise` for soft obligations the audit log should track.
