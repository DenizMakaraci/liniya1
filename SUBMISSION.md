# Devpost submission - copy/paste pack

Deadline: **Fri Oct 30 2026, 10:00 am PT**. Required: track, description, demo URL or test build,
public YouTube video < 3 min, public repo with LICENSE visible in About, README with setup, tools
feedback. Everything in English.

---

## Track
Personal AI Track

## Project name
CallPilot - the phone assistant that knows the law

## Tagline (≤ 60 chars)
Private AI for business calls that reasons about the law

## Description

**Inspiration.** In 2024 the FCC ruled that an AI-generated voice is a "robocall"; Canada's CRTC
already treated automated calling that way, and the EU requires prior consent. That means most AI
phone-agent demos would be unlawful the moment they dial a real consumer - and the company, not the
model, is liable for every hallucinated price. Sales teams want AI on calls; compliance teams say no.
Both are right. We built the assistant a compliance officer would sign off on.

**What it does.** CallPilot is an always-on, private phone assistant with two modes on one brain:
a *copilot* that whispers to a human rep on outbound calls, and an *inbound agent* that answers the
company line. Before any call it detects the jurisdiction from the phone number and decides whether
the call may happen and what must be said first (Canada, Quebec/Law 25, US/TCPA, EU). During the
call every sentence passes a guardrail that only lets the model assert legally approved facts.
It asks permission before promising a callback and stores that consent as evidence; it honours
opt-outs instantly and blocks future outbound calls to that number. After the call it writes a
summary to persistent prospect memory and an audit trail (disclosures made, consent, blocks) as
plain local files. A legal reasoner answers "can we do X?" with citations and says when the rules
don't settle a question.

**How we built it.** Python/FastAPI. One NVIDIA open model - Nemotron 3 Nano (30B MoE, 3B active)
on Nebius Token Factory via the OpenAI-compatible API - runs everything: thinking off on the live
path (~400 ms TTFT whispers and replies, 2 s briefs, a JSON guardrail judge) and thinking on only
for legal reasoning. A deterministic policy engine evaluates a YAML rule base in which every rule
carries its regulator citation; BM25 retrieval feeds the reasoner and the two-tier company knowledge
base (facts the AI may *assert* vs docs it may *answer from*). Prospect memory and consent live in
SQLite; the whole thing is packaged for an NVIDIA OpenShell sandbox via NemoClaw with deny-all
egress except Token Factory, and exposed to a Hermes Agent as 8 MCP tools so an agent must pass the
compliance gate before it dials. Riva adapters (Parakeet ASR, Magpie TTS) and a Twilio Media
Streams bridge are included for live audio.

**Challenges.** Nemotron is a hybrid reasoning model: with a small token budget the answer arrives
empty because the budget is spent thinking. We split the system into a no-think live path and a
think-on reasoning path, and measured rather than guessed - the 120B Super model took 108 s per
brief on the public tier versus 2.3 s for the small model, so we shipped the small one everywhere.
Getting consent capture right was the other hard part: the first version counted "okay, can you
call me back?" as consent; now the AI must ask permission first and the caller must affirm.

**Accomplishments.** A complete loop you can watch: a caller opts out on an inbound call and the
outbound gate refuses that number the next second, with the rule cited. The AI correcting a false
"80% fewer no-shows" claim to the approved "up to 30%" unprompted. A contacts importer that rejects
consent rows without a date and source, because the CRTC requires consent to be demonstrable.
43 tests, all offline.

**What we learned.** Small model + deterministic rules + retrieval beats a big model guessing, both
on latency and on auditability. And "private AI" is mostly a data-layout problem: when every artifact
is a file in a sandbox volume, erasure, retention and audit become `rm`, `cron` and `cat`.

**What's next.** Live phone audio via Twilio + Riva, French mode for Quebec, nemotron-embed
retrieval, a rep-facing web UI, and counsel review of the rule base.

## How we used NVIDIA and Nebius (be specific)

- **Nebius Token Factory**: all inference, `https://api.tokenfactory.nebius.com/v1/`, OpenAI-compatible
  client (`src/callpilot/llm/client.py`). The CLI prints the provider on every run.
- **NVIDIA Nemotron 3 Nano 30B-A3B** (`nvidia/Nemotron-3_5-Lightning`): fast path with
  `enable_thinking: false` (whispers, inbound replies, guardrail judge, call briefs, summaries); legal
  reasoner with thinking on. Model IDs are environment variables; Super/Ultra are drop-in.
- **NVIDIA NemoClaw / OpenShell**: `nemoclaw/Dockerfile` and `nemoclaw/callpilot-policy.yaml` -
  sandbox, deny-all egress except Token Factory, credential injected at the gateway, rw only on `data/`.
- **Hermes Agent**: `src/callpilot/tools/hermes_tools.py` + `mcp_server.py` - 8 tools including
  `compliance_check_call`, `compliance_ask`, `knowledge_search`, memory and audit.
- **NVIDIA Riva** (Parakeet ASR, Magpie TTS): adapters in `src/callpilot/call/asr.py`, `tts.py`.

## Demo / test build
Public repo + `README.md` quick start (3 minutes with a Token Factory key; offline mode without one).
Optional: `callpilot serve` exposes REST + WebSocket (`/docs`).

## Video (< 3 min) - shot list
0:00 Problem: FCC/CRTC - AI voice = robocall; company liable for what the AI says.
0:20 `callpilot check` Quebec consumer -> BLOCKED with CRTC citation; with callback consent -> ALLOWED,
     Law 25 lines. Point at the provider banner: Nebius Token Factory / Nemotron.
0:55 `callpilot inbound-demo --script callback`: spoken disclosures, FAQ answer, 80% -> 30% correction,
     permission before callback, consent captured.
1:50 `--script optout` then `check` -> BLOCKED; `--direction inbound` -> still ALLOWED.
2:15 `ls data`, audit JSONL, import rejecting undocumented consent, OpenShell policy file.
2:40 Architecture slide: one Nemotron model, thinking on/off split, measured numbers. Close.

## Tools feedback (also enters the feedback prize)

**Nebius Token Factory.** The OpenAI-compatible API meant zero integration work; swapping between
providers was a one-line base-URL change, which is exactly what you want when a hackathon rule says
"must run on Nebius". Two things would help: (1) a documented, stable way to toggle Nemotron's
reasoning (`chat_template_kwargs.enable_thinking` works but is undocumented in the catalog), and
(2) per-model latency SLOs or a "fast tier" flag in the catalog - public-endpoint tail latency on
larger Nemotron models makes them hard to use interactively. Billing requiring a card before the
first call is a real hurdle for hackathon participants.

**NVIDIA Nemotron.** The hybrid reasoning design is excellent for this use case: one small model
serves a 400 ms live path *and* a deliberate reasoning path. Gotcha: when `max_tokens` is exhausted
during thinking, `content` is `None` and the answer lives in `reasoning_content`; this should be
called out prominently in the model card. Nano/Lightning occasionally repeat a question in short
multi-turn exchanges; a few-shot example in the system prompt fixed most of it.

**NemoClaw / OpenShell.** The architecture docs are clear about *what* the pieces do, but the policy
YAML schema is not published in a single reference page, so we wrote our policy from the described
semantics and could not validate it offline. A `openshell policy validate` command and a minimal
"bring your own container" example would have let us ship a verified sandbox instead of a designed one.

**Hermes Agent.** OpenAI function-calling schemas plus a stdio MCP server were enough to expose our
skills; a one-page "add a local MCP tool to Hermes inside NemoClaw" guide would save an hour.

## Devpost checklist
- [ ] Repo public, LICENSE (MIT) visible in the About box
- [ ] README setup steps verified on a clean clone
- [ ] YouTube video public, < 3 min, link in README and form
- [ ] `.env` NOT committed (gitignored); demo key rotated after the event
- [ ] Track = Personal AI; description, NVIDIA/Nebius usage, feedback pasted
- [ ] Submitted before 10:00 am PT
