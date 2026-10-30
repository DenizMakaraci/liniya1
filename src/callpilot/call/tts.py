"""Streaming TTS. Production: NVIDIA Riva / Magpie TTS NIM over gRPC (first audio in ~100-200 ms).
Development: MockTTS yields no audio; the session still emits the text so demos and tests work.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from ..config import settings


class TTS(Protocol):
    sample_rate: int

    def synthesize(self, text: str, language: str = "en-US") -> AsyncIterator[bytes]: ...


class MockTTS:
    sample_rate = 16000

    async def synthesize(self, text: str, language: str = "en-US") -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator


class RivaTTS:
    """Riva streaming synthesis; yields PCM16 chunks as they are produced."""

    def __init__(self, server: str, voice: str = "Magpie-Multilingual.EN-US.Sofia", sample_rate: int = 16000):
        import riva.client  # optional dependency: pip install 'callpilot[speech]'

        self.svc = riva.client.SpeechSynthesisService(riva.client.Auth(uri=server))
        self.voice, self.sample_rate = voice, sample_rate

    async def synthesize(self, text: str, language: str = "en-US") -> AsyncIterator[bytes]:
        import asyncio

        import riva.client

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes | None] = asyncio.Queue()

        def run():
            try:
                for resp in self.svc.synthesize_online(
                        text, self.voice, language, sample_rate_hz=self.sample_rate,
                        encoding=riva.client.AudioEncoding.LINEAR_PCM):
                    loop.call_soon_threadsafe(q.put_nowait, resp.audio)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            while (chunk := await q.get()) is not None:
                yield chunk
        finally:
            task.cancel()


def make_tts() -> TTS:
    return RivaTTS(settings.riva_server) if settings.riva_server else MockTTS()
