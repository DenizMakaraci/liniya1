# CallPilot - demo runbook (6-7 minutes)

## Before you walk up

```bash
cd callpilot
./scripts/demo-reset.sh            # clean data, import fictional contacts, seed one returning prospect
.venv/bin/python -m pytest -q      # expect "43 passed"
.venv/bin/callpilot ask "ping" >/dev/null && echo API OK   # warms the endpoint, confirms the key
```

- Terminal font large (18pt+), dark theme, window ~100 columns.
- Open `pitch/slides.md` in Marp (VS Code Marp extension, or `npx @marp-team/marp-cli slides.md -o slides.pdf`).
- Have `pitch/fallback.mp4` (record a full run beforehand) ready in case the API stalls.
- Pin times with `--at` so calling hours never surprise you.

Alias to keep commands short on screen:

```bash
alias cp=".venv/bin/callpilot"
```

---

## 0:00 - Hook (30 s) - slide 2

> "Last year the FCC ruled that an AI voice is a robocall. Canada's CRTC already treated it that way. Which means most AI phone-agent demos you'll see today would be illegal the moment they dial a real consumer. We built the one that knows that."

---

## 0:30 - Scene 1: the gate (90 s) - slide 4

```bash
cp check +15145550123 --mode autonomous --audience consumer --no-explain
```

Point at: **CA-QC detected from the area code**, BLOCKED, the `[ca.adad.consent]` reason with its CRTC section, the Quebec Law 25 lines in "Must say first".

```bash
cp check +15145550123 --mode autonomous --consent callback_request --no-explain --at 2026-10-30T14:00-04:00
```

Point at: ALLOWED, the note "permitted only because of callback_request consent", the AI-disclosure line.

Then one call with the reasoner (takes ~8 s - talk over it):

```bash
cp ask "Can an AI call businesses in Canada without consent?" --jurisdiction CA
```

> "Notice it says the rules don't settle this and tells us to confirm with counsel. We mark unclear rules as unclear; the model doesn't bluff."

---

## 2:00 - Scene 2: the AI answers the phone (2 min) - slide 5

```bash
cp inbound-demo +16045550142 --script callback
```

While it runs, narrate in order:
1. Mandatory opening spoken by the AI - *every line audited*.
2. "Does it work with Outlook?" -> answer from approved facts.
3. "80 percent?" -> **the AI corrects it to 30%**. "That's the guardrail plus approved facts: the company is liable for what the AI says, so it only says what the company signed off on."
4. "Call me back tomorrow?" -> AI asks **permission**. "Yes please" -> `Consent captured`. "That yes is what will make the outbound callback legal. We store the verbatim utterance."

If the model is slow, the brief is already printed - point at the **Do-not-say** section while waiting.

---

## 4:00 - Scene 3: the loop closes (60 s) - slide 6

```bash
cp inbound-demo +16045550199 --script optout
cp check +16045550199 --scrubbed --no-explain --at 2026-10-30T14:00-07:00
cp check +16045550199 --direction inbound --mode autonomous --no-explain | head -2
```

> "One sentence from the caller, and the whole system changes what it's allowed to do. Outbound: blocked. Inbound: still fine - they can always call us."

---

## 5:00 - Scene 4: data stays home (60 s) - slide 9

```bash
ls data data/audit
cat data/audit/$(ls -t data/audit | head -1) | .venv/bin/python -m json.tool --no-ensure-ascii | grep -E '"event"|"obligation"|"kind"' | head -12
cp import contacts examples/contacts.csv
```

Point at: the rejected rows - *"consent claimed without consent_date and consent_source (not demonstrable)"*.

> "No upload step exists. Files on disk, inside an OpenShell sandbox with deny-all egress. The agent never sees the API key."

Show `nemoclaw/callpilot-policy.yaml` for 10 seconds if time allows.

---

## 6:00 - Model + close (45 s) - slides 8, 12

> "All of this is one open model: Nemotron 3.5 Lightning, 3B active. Thinking off on the live path - 400 ms; thinking on only for legal reasoning. We measured the 120B model: 108 seconds for a brief. We didn't ship it. Small, fast, and runnable on-prem is the whole point of personal AI."

Close on slide 12.

---

## Likely questions

| Q | A |
|---|---|
| Is it legal advice? | No - a reasoning aid over regulator text, with citations and explicit "not settled" flags. Counsel reviews the rule base. |
| Why not real phone audio? | Adapters and Twilio bridge are written; we prioritised the compliance brain and kept audio out of the critical demo path. One day of work to first live call. |
| Why not a bigger model? | Measured. 108 s vs 2.3 s. The small model plus deterministic rules beats a big model guessing. |
| How do you stop the model hallucinating a price? | Two-tier knowledge: facts it may assert vs docs it may answer from, plus a JSON judge on every sentence containing a number. |
| What if the caller speaks French? | Profile declares languages; the Quebec rule injects "continue in French". Prompts are English today - roadmap. |
| Does inbound need consent? | No - inbound isn't an unsolicited telecom. It needs disclosure (AI, recording, Law 25 notices), which the AI speaks itself. |
| What about the US? | TCPA + the FCC 2024 AI-voice ruling are in `rules/us.yaml`; autonomous outbound needs prior express *written* consent there. |

## If the API is down

Every `check` command is offline and still works. `ask` and the demos fall back to offline text (labelled). Play `fallback.mp4` for Scenes 2-3 and narrate.

## After the event

Rotate the API key (it was shared in chat). Replace fictional Acme data before any real use.
