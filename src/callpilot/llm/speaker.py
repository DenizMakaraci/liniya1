"""Speaker path: the AI *is* the voice on an inbound call. Nemotron Lightning, thinking OFF,
streaming, short turns. Emits optional control tags at the END of a reply which the session strips
and acts on deterministically:

  [HANDOFF]      caller wants a person / topic needs a licensed human / AI is unsure
  [CALLBACK_OK]  caller clearly agreed to be called back (session records consent evidence)
  [END]          conversation is naturally over
"""

import re
from collections.abc import AsyncIterator

from ..config import settings
from .client import NO_THINK

TAGS = ("HANDOFF", "CALLBACK_OK", "END")
TAG_RE = re.compile(r"\s*\[(HANDOFF|CALLBACK_OK|END)\]\s*$")

SYSTEM = """You are {assistant_name}, an AI phone assistant answering calls for {company}.
Speak naturally and briefly: 1-2 short sentences per turn, plain spoken language, no lists, no markdown.
You already said the legally required opening (AI disclosure, recording notice); don't repeat it.

What you can do: answer questions using ONLY the APPROVED FACTS, qualify the caller's need, take a
message, and offer a callback or an appointment with a human.

Rules:
- Never invent prices, statistics, availability or guarantees. If a fact is not in APPROVED FACTS say
  you'll have a colleague confirm it.
- Never give individual financial, insurance, medical or legal advice. Offer a licensed human instead.
- If the caller asks for a human, is upset, or you are unsure twice in a row, hand off.
- If the caller asks not to be contacted again, agree, confirm, and end.
- Respect the COMPLIANCE NOTES exactly.
- Before offering a callback, ask for permission and the best number; only treat it as agreed when the
  caller clearly says yes.

Control tags: append exactly one of [HANDOFF], [CALLBACK_OK] or [END] at the very end of your reply
when applicable; otherwise append nothing."""


class Speaker:
    def __init__(self, client, model: str | None = None, assistant_name: str = "Ada"):
        self.client = client
        self.model = model or settings.fast_model
        self.assistant_name = assistant_name

    async def reply(self, brief: str, approved: list[str], compliance_notes: str,
                    transcript: list[dict], max_turns: int = 12) -> AsyncIterator[str]:
        if self.client is None:
            yield _offline_reply(transcript)
            return
        turns = "\n".join(f"{'CALLER' if t['speaker'] == 'caller' else 'YOU'}: {t['text']}"
                          for t in transcript[-max_turns:])
        user = (f"CALL BRIEF:\n{brief}\n\nAPPROVED FACTS:\n- " + "\n- ".join(approved) +
                f"\n\nCOMPLIANCE NOTES:\n{compliance_notes}\n\nCONVERSATION:\n{turns}\n\nYOU:")
        stream = await self.client.chat.completions.create(
            model=self.model, stream=True, temperature=0.5, max_tokens=90,
            messages=[{"role": "system", "content": SYSTEM.format(
                assistant_name=self.assistant_name, company=settings.company_name)},
                {"role": "user", "content": user}],
            extra_body=NO_THINK,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


def split_tags(text: str) -> tuple[str, set[str]]:
    tags: set[str] = set()
    while (m := TAG_RE.search(text)):
        tags.add(m.group(1))
        text = text[: m.start()]
    return text.strip(), tags


def _offline_reply(transcript: list[dict]) -> str:
    last = next((t["text"].lower() for t in reversed(transcript) if t["speaker"] == "caller"), "")
    if re.search(r"\b(human|person|agent|someone real|representative)\b", last):
        return "Of course, let me connect you with a colleague. [HANDOFF]"
    if re.search(r"\bcall (me )?back\b", last):
        return "I can arrange that. May I have your permission to call you back on this number?"
    if re.search(r"\b(yes|sure|okay|ok|please)\b", last):
        return "Great, a colleague will call you back on this number. Thanks for calling. [CALLBACK_OK]"
    if re.search(r"\b(bye|goodbye|that'?s all|thanks,? that'?s it)\b", last):
        return "Thanks for calling, have a good day. [END]"
    return "I can help with that. Could you tell me a bit more about what you need?"
