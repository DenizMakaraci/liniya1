"""Company knowledge: two tiers with different trust levels.

  knowledge/facts.md      APPROVED FACTS - the only statements the AI may *assert* (prices, features,
                          statistics, guarantees). Short, legal-reviewed, versioned. Ground truth for
                          the guardrail judge.
  knowledge/docs/*        DOCUMENTS - FAQs, service descriptions, policies. Chunked and retrieved per
                          turn so the AI can *answer* from them; a number that appears only in a doc
                          must still pass the approved-facts check before it is spoken.

Retrieval is BM25 (offline, deterministic). Swap `_tok`/`BM25Okapi` for nemotron-3.5-embed-1b when
semantic recall matters more than auditability.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

from ..config import settings

DOC_EXTS = {".md", ".txt", ".markdown"}


@dataclass
class Chunk:
    doc: str
    heading: str
    text: str

    def render(self) -> str:
        return f"[{self.doc} > {self.heading}]\n{self.text}" if self.heading else f"[{self.doc}]\n{self.text}"


class KnowledgeBase:
    def __init__(self, facts: list[str], chunks: list[Chunk]):
        self.facts = facts
        self.chunks = chunks
        self.bm25 = BM25Okapi([_tok(f"{c.doc} {c.heading} {c.text}") for c in chunks]) if chunks else None

    def retrieve(self, query: str, k: int = 3, min_score: float = 0.5) -> list[Chunk]:
        if not self.bm25 or not query.strip():
            return []
        scores = self.bm25.get_scores(_tok(query))
        ranked = sorted(zip(scores, self.chunks), key=lambda t: -t[0])
        return [c for s, c in ranked[:k] if s >= min_score]

    def context_for(self, query: str, k: int = 3, max_chars: int = 1500) -> str:
        out, used = [], 0
        for c in self.retrieve(query, k):
            r = c.render()
            if used + len(r) > max_chars:
                break
            out.append(r)
            used += len(r)
        return "\n\n".join(out)

    def overview(self, max_chars: int = 1200) -> str:
        """Doc titles + headings, for the pre-call brief."""
        seen, lines = set(), []
        for c in self.chunks:
            key = (c.doc, c.heading)
            if key not in seen:
                seen.add(key)
                lines.append(f"- {c.doc}: {c.heading}" if c.heading else f"- {c.doc}")
        return "\n".join(lines)[:max_chars]

    @property
    def stats(self) -> dict:
        return {"facts": len(self.facts), "docs": len({c.doc for c in self.chunks}), "chunks": len(self.chunks)}


def parse_facts(text: str) -> list[str]:
    facts = []
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
        if s:
            facts.append(s)
    return facts


def chunk_markdown(doc: str, text: str, max_chars: int = 700) -> list[Chunk]:
    """Split on headings, then on blank lines until chunks fit."""
    chunks: list[Chunk] = []
    heading = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        paras, cur = re.split(r"\n\s*\n", body), ""
        for p in paras:
            if cur and len(cur) + len(p) > max_chars:
                chunks.append(Chunk(doc, heading, cur.strip()))
                cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
        if cur.strip():
            chunks.append(Chunk(doc, heading, cur.strip()))

    for line in text.splitlines():
        if m := re.match(r"^#{1,6}\s+(.*)", line):
            flush()
            heading = m.group(1).strip()
        else:
            buf.append(line)
    flush()
    return chunks


def _read_doc(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader  # optional: pip install pypdf
        except ImportError:
            return ""
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return path.read_text(errors="ignore")


@lru_cache(maxsize=4)
def load_kb(knowledge_dir: Path | None = None) -> KnowledgeBase:
    kd = knowledge_dir or settings.knowledge_dir
    facts_file = kd / "facts.md"
    facts = parse_facts(facts_file.read_text()) if facts_file.exists() else []
    chunks: list[Chunk] = []
    docs_dir = kd / "docs"
    if docs_dir.exists():
        for p in sorted(docs_dir.rglob("*")):
            if p.name.lower() == "readme.md":
                continue
            if p.is_file() and (p.suffix.lower() in DOC_EXTS or p.suffix.lower() == ".pdf"):
                text = _read_doc(p)
                if text.strip():
                    chunks += chunk_markdown(p.stem.replace("-", " ").replace("_", " "), text)
    return KnowledgeBase(facts, chunks)


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())
