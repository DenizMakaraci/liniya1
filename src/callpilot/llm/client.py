"""OpenAI-compatible async client for Nebius Token Factory / build.nvidia.com / any vLLM or NIM.

Inside an OpenShell sandbox the API key is a placeholder; the gateway's L7 proxy swaps in the real
credential at the network boundary, so the agent process never sees it.

Nemotron 3.x are hybrid reasoning models: with thinking on, the endpoint returns the chain of thought
in `message.reasoning_content` and the answer in `message.content`; if `max_tokens` runs out during
thinking, `content` is None. `complete()` handles both modes.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from ..config import settings

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI | None:
    if not settings.nebius_api_key:
        return None
    return AsyncOpenAI(api_key=settings.nebius_api_key, base_url=settings.nebius_base_url, timeout=60)


async def complete(client: AsyncOpenAI, model: str, messages: list[dict], *, think: bool,
                   max_tokens: int, **kw) -> str:
    """Single non-streaming completion. With think=True the budget covers reasoning + answer."""
    extra = kw.pop("extra_body", {})
    if not think:
        extra = {**extra, **NO_THINK}
    resp = await client.chat.completions.create(
        model=model, messages=messages, extra_body=extra or None,
        max_tokens=max_tokens * (4 if think else 1), **kw)
    msg = resp.choices[0].message
    if msg.content:
        return msg.content.strip()
    reasoning = getattr(msg, "reasoning_content", None) or ""
    if reasoning:  # ran out of budget mid-thought: return the tail so the caller still gets something
        return "(answer truncated; model reasoning follows)\n" + reasoning.strip()[-1500:]
    return ""
