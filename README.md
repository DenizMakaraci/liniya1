# CallPilot

A private, always-on cold-call copilot that **reasons about the law before, during and after every
call**. A human rep dials and speaks; the AI listens, whispers what to say next, and refuses to let
the call happen if it would be unlawful in the prospect's jurisdiction.

Built for the Personal AI track: NVIDIA Nemotron (open weights) served on Nebius Token Factory,
sandboxed with NVIDIA NemoClaw / OpenShell, exposed to a Hermes Agent as tools, with all prospect
data kept locally.

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

- Fast path: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, thinking disabled, streaming, max 80 tokens.
- Slow path: `nvidia/Nemotron-3-Ultra-550b-a55b` for the brief, refreshes, summary and legal reasoning.
- Both via the OpenAI-compatible endpoint `https://api.tokenfactory.nebius.com/v1/` (zero retention, EU).

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
rules/                      legal rule KB (default, ca, ca-qc, us, eu) + approved_claims.txt
src/callpilot/compliance/   jurisdiction, rules, engine (gate), guardrail, audit
src/callpilot/llm/          Nebius client, fast path, slow path, legal RAG reasoner
src/callpilot/call/         ASR (mock + Riva), prospect memory, CopilotSession orchestrator
src/callpilot/tools/        Hermes/OpenAI tool schemas + MCP server
src/callpilot/server.py     FastAPI REST + WebSocket
src/callpilot/cli.py        check / ask / demo / serve
nemoclaw/                   Dockerfile + OpenShell policy
tests/                      24 tests for the compliance engine, guardrail and reasoner
```

## Extending the rule base

Add a `rules/<code>.yaml` (e.g. `uk.yaml`), map numbers to it in
`compliance/jurisdiction.py`, and write rules. Every rule needs a `source` with a URL so the
reasoner's citations resolve. Prefer `effect: forbid` + `unless_consent` for consent-gated
activities, `require` + `disclosure` for things that must be said, `restrict` + `hours` for time
windows, and `advise` for soft obligations the audit log should track.
