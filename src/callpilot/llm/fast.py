"""Fast path: Nemotron Nano, reasoning OFF, streaming. Produces the next whisper for the rep.

Latency budget ~400ms TTFT on Nebius public endpoint. Keep the prompt small: brief + last N turns.
"""

from collections.abc import AsyncIterator

from ..config import settings

SYSTEM = """You are a real-time sales copilot whispering to a human rep during a live call.
Output ONE short suggestion (max 2 sentences) the rep can say next. No preamble, no options, no markdown.
Hard rules:
- Use only facts from the CALL BRIEF. Never invent prices, statistics or guarantees.
- If the prospect objects, acknowledge first, then one concise response.
- If the prospect asks not to be called or seems distressed, suggest politely ending the call.
- Respect the COMPLIANCE NOTES exactly.
{extra}"""


class FastPath:
    def __init__(self, client, model: str | None = None):
        self.client = client
        self.model = model or settings.fast_model

    async def suggest(self, brief: str, compliance_notes: str, transcript: list[dict],
                      max_turns: int = 8) -> AsyncIterator[str]:
        if self.client is None:
            yield "[no LLM configured] Acknowledge what they said and ask one open question."
            return
        turns = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in transcript[-max_turns:])
        user = f"CALL BRIEF:\n{brief}\n\nCOMPLIANCE NOTES:\n{compliance_notes}\n\nTRANSCRIPT:\n{turns}\n\nNext whisper:"
        stream = await self.client.chat.completions.create(
            model=self.model, stream=True, temperature=0.4, max_tokens=80,
            messages=[{"role": "system", "content": SYSTEM.format(extra="/no_think")},
                      {"role": "user", "content": user}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
