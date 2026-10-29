"""Streaming ASR. Production: NVIDIA Riva / Parakeet (or Nemotron-3.5-ASR-Streaming) NIM over gRPC,
~20ms per 160ms chunk. Development: MockASR that accepts text lines as if they were final transcripts.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from ..config import settings


@dataclass
class Transcript:
    speaker: str  # "rep" | "prospect"
    text: str
    is_final: bool


class ASR(Protocol):
    async def feed(self, speaker: str, audio: bytes) -> None: ...
    def results(self) -> AsyncIterator[Transcript]: ...
    async def close(self) -> None: ...


class MockASR:
    """Text stands in for audio. `feed(speaker, b"hello")` yields a final transcript."""

    def __init__(self):
        self.q: asyncio.Queue[Transcript | None] = asyncio.Queue()

    async def feed(self, speaker: str, audio: bytes) -> None:
        await self.q.put(Transcript(speaker, audio.decode(errors="ignore").strip(), True))

    async def results(self) -> AsyncIterator[Transcript]:
        while (t := await self.q.get()) is not None:
            yield t

    async def close(self) -> None:
        await self.q.put(None)


class RivaASR:
    """One Riva streaming recognizer per speaker channel (rep mic / prospect line)."""

    def __init__(self, server: str, language: str = "en-US", sample_rate: int = 16000):
        import riva.client  # optional dependency: pip install 'callpilot[speech]'

        self.riva = riva.client
        self.auth = riva.client.Auth(uri=server)
        self.svc = riva.client.ASRService(self.auth)
        self.cfg = riva.client.StreamingRecognitionConfig(
            config=riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM, sample_rate_hertz=sample_rate,
                language_code=language, max_alternatives=1, enable_automatic_punctuation=True),
            interim_results=True)
        self.inputs: dict[str, asyncio.Queue[bytes | None]] = {}
        self.out: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []

    async def feed(self, speaker: str, audio: bytes) -> None:
        if speaker not in self.inputs:
            self.inputs[speaker] = asyncio.Queue()
            self.tasks.append(asyncio.create_task(self._run(speaker)))
        await self.inputs[speaker].put(audio)

    async def _run(self, speaker: str):
        loop = asyncio.get_running_loop()
        q = self.inputs[speaker]

        def chunks():
            while (c := asyncio.run_coroutine_threadsafe(q.get(), loop).result()) is not None:
                yield c

        def consume():
            for resp in self.svc.streaming_response_generator(audio_chunks=chunks(), streaming_config=self.cfg):
                for r in resp.results:
                    if r.alternatives:
                        loop.call_soon_threadsafe(
                            self.out.put_nowait, Transcript(speaker, r.alternatives[0].transcript, r.is_final))

        await asyncio.to_thread(consume)

    async def results(self) -> AsyncIterator[Transcript]:
        while (t := await self.out.get()) is not None:
            yield t

    async def close(self) -> None:
        for q in self.inputs.values():
            await q.put(None)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.out.put(None)


def make_asr() -> ASR:
    return RivaASR(settings.riva_server) if settings.riva_server else MockASR()
