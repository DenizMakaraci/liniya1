"""OpenAI-compatible async client for Nebius Token Factory (or any vLLM/NIM endpoint).

Inside an OpenShell sandbox the API key is a placeholder; the gateway's L7 proxy swaps in the real
credential at the network boundary, so the agent process never sees it.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from ..config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI | None:
    if not settings.nebius_api_key:
        return None
    return AsyncOpenAI(api_key=settings.nebius_api_key, base_url=settings.nebius_base_url, timeout=30)
