---
marp: true
theme: default
paginate: true
style: |
  section { font-size: 26px; }
  h1 { color: #76b900; }
  h2 { color: #1a1a1a; }
  table { font-size: 20px; }
  code { font-size: 20px; }
  .small { font-size: 18px; color: #555; }
---

# CallPilot

## The phone assistant that knows the law

A private, always-on AI for business phone calls.
It coaches your reps, answers your inbound line, and refuses to break the rules.

**NVIDIA Nemotron 3.5 Lightning · NemoClaw / OpenShell · Hermes Agent tools**

<span class="small">Personal AI Track - all demo data is fictional</span>

---

# The problem

Every business phone call is a legal minefield, and it changed last year.

- **AI voices are now "robocalls."** CRTC (Canada), FCC (US, Feb 2024 ruling), EU ePrivacy: an AI dialing a consumer without express consent is illegal.
- **Rules differ by phone number.** Quebec adds Law 25 notices and French; the US has different hours; Germany is opt-in.
- **Privacy law covers the transcript**, not just the recording.
- **The company is liable for what the AI says.** One hallucinated discount is a misleading representation.

Sales teams want AI on calls. Compliance teams say no. Both are right.

---

# What we built

An assistant that **reasons about the law before, during and after every call** - and keeps every byte of customer data on your own machine.

| | |
|---|---|
| **Pre-call gate** | number -> jurisdiction -> *may this call happen, and what must be said first?* |
| **Live guardrail** | every sentence checked against approved facts and hard rules before it is spoken |
| **Legal reasoner** | plain-language explanation with citations; knows when the rules *don't* settle a question |
| **Audit trail** | what was disclosed, by whom, what consent exists, what was blocked - as plain files |

Two modes on one brain: **copilot** (human dials, AI whispers) and **inbound** (AI answers).

---

# Demo 1 - the gate

```
$ callpilot check +1514555… --mode autonomous --audience consumer
Jurisdiction : CA-QC (Quebec)
Decision     : BLOCKED
  blocked by : [ca.adad.consent] An automated/AI voice may not initiate a
               solicitation call without the consumer's prior express consent…
  blocked by : [ca.dncl.scrub] number has not been scrubbed against the DNCL
```

```
$ callpilot check +1514555… --mode autonomous --consent callback_request
Decision     : ALLOWED
Must say first:
  - This is an automated call on behalf of Acme Inc., 123 Main St… reach us at +1 800…
  - This call is handled by an AI assistant on behalf of Acme Inc.
  - Decisions during this call may be made automatically; you can ask for a person…
```

Deterministic. Every rule cites the regulator's text. Quebec detected from the area code.

---

# Demo 2 - the AI answers the phone

```
caller > Hi, I need to change my billing details.
AI     > Log in to your Acme account, open Settings, then Billing, and choose
         "Update payment method". Changes apply to the next invoice.
caller > Is it true it cuts no-shows by 80 percent?
AI     > No, it reduces missed appointments by up to 30% based on our 2025
         customer survey.
caller > Can someone call me back tomorrow?
AI     > I can arrange that. May I have your permission to call you back on this number?
caller > Yes please.
         -> consent_captured: callback_request, verbatim utterance, audit + memory
```

Answers from the company's own FAQ. Corrects a false claim unprompted.
Asks permission before a callback - and that "yes" is what makes the *outbound* call legal later.

---

# Demo 3 - the loop closes

```
caller > Please remove my number and don't contact me again.
AI     > Understood, I've noted that you don't want to be contacted. Goodbye.
         -> opt_out -> internal do-not-call list
```

```
$ callpilot check +1604555…           # same number, next day, outbound
Decision     : BLOCKED
  blocked by : [default.optout_honour] prospect previously asked not to be contacted
```

```
$ callpilot check +1604555… --direction inbound --mode autonomous
Decision     : ALLOWED                 # they can still call us
```

One sentence from a caller changes what the whole system is allowed to do.

---

# How the legal reasoning works

| Layer | When | How | Latency |
|---|---|---|---|
| Jurisdiction gate | before dialing | deterministic engine over a YAML rule base with citations | < 1 ms |
| Legal reasoner | before / on demand | BM25 retrieval -> Nemotron, **thinking on**, cites `[rule-id]` | 7-12 s, off the call path |
| Live guardrail | every sentence | regex hard-blocks + Nemotron JSON judge, **thinking off** | ~1 ms + ~400 ms |
| Audit log | always | JSONL per call; consent, disclosures, blocks, retention | - |

Rules marked `confidence: unclear` (e.g. AI calling *businesses* in Canada) are surfaced as open questions, not silently decided. The model says "not settled - confirm with counsel."

---

# One open model does everything

**`nvidia/nemotron-3.5-lightning-30b-a3b`** - 30B MoE, 3B active, hybrid reasoning.

| Task | Thinking | Measured |
|---|---|---|
| Whispers / replies (90 tok) | off | ~400 ms TTFT |
| Call brief | off | 2.3 s |
| Guardrail judge | off | ~400 ms, only when a claim needs grounding |
| Legal reasoning | **on** | 7-12 s |

We benchmarked Super VL 120B: same brief took **108 s** with thinking. Not shipped.

Small-model-everywhere is the point: cheap, fast, and runnable on-prem.
Served today via build.nvidia.com; one env line switches to Nebius Token Factory or a local NIM.

---

# Your data never leaves

```
knowledge/facts.md      what the AI may ASSERT   (prices, stats)   read-only
knowledge/docs/         what the AI may ANSWER FROM (FAQs)          read-only
company.yaml            who it speaks for                           read-only
data/memory.sqlite      prospects, history, consent evidence        rw
data/audit/*.jsonl      every disclosure, block, consent, opt-out   rw
data/internal_dncl.txt  do-not-call                                 rw
```

- Runs in an **OpenShell sandbox via NemoClaw**: deny-all egress except inference; the API key is injected by the gateway - the agent only ever sees a placeholder.
- Contacts import **rejects consent without date + source** - the CRTC "demonstrable" standard, enforced at the door.
- `callpilot forget <number>` - right to erasure. Retention purge every call.
- Exposed to a **Hermes Agent** as 8 MCP tools: it must call `compliance_check_call` before it dials.

---

# Architecture

```
          ┌──────────── before the call ────────────┐
 number ─>│ jurisdiction gate ─> policy ─> reasoner  │─> opening script, obligations, brief
          └─────────────────────────────────────────┘
 caller ──> ASR (Parakeet) ──> transcript ─┬─> Nemotron Lightning (no-think) ─> guardrail ─> TTS / rep screen
                                           └─> opt-out / human-request / consent detectors
          ┌──────────── after the call ─────────────┐
          │ summary ─> memory ; audit ; retention    │
          └─────────────────────────────────────────┘
```

Python · FastAPI · WebSockets · SQLite · rank-bm25 · phonenumbers · OpenAI-compatible client
43 tests. Twilio Media Streams bridge and Riva adapters included.

---

# What's real, what's next

**Working today (shown live)**
Gate, reasoner, copilot, inbound agent, knowledge, importers, audit, memory, MCP tools.

**Written, not yet exercised**
Riva/Parakeet/Magpie adapters against hosted NIMs; Twilio bridge against live audio; NemoClaw onboarding.

**Honest caveats**
Rule texts paraphrased from regulator sources - needs counsel review before real calls.
Free-tier latency spikes; we run a 3B-active model to stay inside them.

**Next**
Live phone via Twilio + Riva · French mode for Quebec · semantic retrieval (nemotron-embed) · rep web UI.

---

# Why this matters

Most "AI phone agent" demos would be **illegal to run** in Canada, the US or the EU the moment they dial a real consumer.

CallPilot is the version a compliance officer would sign off on:
it knows which calls it may make, says what the law requires, says only what the company approved, remembers who said no - and proves all of it in a file you can read.

**Private. Always on. Knows the law.**

<span class="small">github: callpilot · built with NVIDIA Nemotron, NemoClaw/OpenShell, Hermes Agent</span>
