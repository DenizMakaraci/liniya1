"""Legal reasoner: RAG over the rule knowledge base + the slow (reasoning) model.

Used for (a) the open questions the deterministic engine cannot settle (e.g. AI calling a business
in Canada), (b) ad-hoc "can we do X?" questions from the rep or the Hermes agent, and (c) explaining
a policy decision in plain language with citations. Retrieval is BM25 over rule text so it works
offline and deterministically; the LLM only ever sees retrieved rules, never the whole KB.
"""

import re

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from ..compliance.engine import CallPolicy
from ..compliance.rules import Rule, RuleBook
from ..config import settings

SYSTEM = """You are a telemarketing-compliance analyst. Answer using ONLY the numbered rules provided.
Cite rules inline as [rule-id]. If the rules do not settle the question, say so explicitly and
recommend the conservative course of action plus "confirm with counsel". Be concise (under 200 words).
You are not giving legal advice; say so in one short closing line."""


class Citation(BaseModel):
    rule_id: str
    summary: str
    section: str = ""
    source_title: str = ""
    source_url: str = ""


class LegalAnswer(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    settled: bool


class LegalReasoner:
    def __init__(self, rulebook: RuleBook, client=None, model: str | None = None):
        self.rulebook = rulebook
        self.client = client
        self.model = model or settings.slow_model
        self.rules: list[Rule] = rulebook.all_rules()
        self.bm25 = BM25Okapi([_tok(f"{r.id} {r.summary} {r.text} {r.section}") for r in self.rules])

    def retrieve(self, query: str, jurisdiction: str | None = None, k: int = 6) -> list[Rule]:
        scores = self.bm25.get_scores(_tok(query))
        ranked = sorted(zip(scores, self.rules), key=lambda t: -t[0])
        if jurisdiction:
            allowed = {r.id for r in self.rulebook.for_jurisdiction(jurisdiction)}
            ranked = [t for t in ranked if t[1].id in allowed]
        return [r for s, r in ranked[:k] if s > 0]

    async def ask(self, question: str, jurisdiction: str | None = None,
                  policy: CallPolicy | None = None) -> LegalAnswer:
        rules = self.retrieve(question, jurisdiction)
        if policy:  # make sure every applied rule is in context too
            ids = {r.id for r in rules}
            rules += [self.rulebook.by_id[a.id] for a in policy.applied_rules
                      if a.id in self.rulebook.by_id and a.id not in ids]
        cites = [self._cite(r) for r in rules]
        settled = all(r.confidence == "settled" for r in rules)

        if self.client is None:
            answer = self._offline_answer(rules, policy)
            return LegalAnswer(question=question, answer=answer, citations=cites, settled=settled)

        ctx = "\n".join(
            f"{i+1}. [{r.id}] ({r.effect}, {r.confidence}) {r.summary}"
            + (f"\n   Source: {r.section}. {r.text.strip()}" if r.text else "")
            for i, r in enumerate(rules)
        )
        extra = ""
        if policy:
            extra = (f"\n\nEngine decision for this call: allowed={policy.allowed}; "
                     f"blocking={policy.blocking_reasons}; open_questions={policy.open_questions}")
        resp = await self.client.chat.completions.create(
            model=self.model, temperature=0.1, max_tokens=600,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"RULES:\n{ctx}{extra}\n\nQUESTION: {question}"}],
        )
        return LegalAnswer(question=question, answer=resp.choices[0].message.content.strip(),
                           citations=cites, settled=settled)

    async def explain_policy(self, policy: CallPolicy) -> LegalAnswer:
        q = ("Explain to the sales rep, in plain language, why this call is "
             + ("allowed and what they must say and do." if policy.allowed else "blocked and what would make it allowed."))
        return await self.ask(q, policy.jurisdiction, policy)

    def _cite(self, r: Rule) -> Citation:
        s = self.rulebook.sources.get(r.source)
        return Citation(rule_id=r.id, summary=r.summary, section=r.section,
                        source_title=s.title if s else r.source, source_url=s.url if s else "")

    @staticmethod
    def _offline_answer(rules: list[Rule], policy: CallPolicy | None) -> str:
        lines = []
        if policy:
            lines.append("Engine decision: " + ("ALLOWED" if policy.allowed else "BLOCKED"))
            lines += [f"  - {b}" for b in policy.blocking_reasons]
        lines.append("Relevant rules:")
        lines += [f"  - [{r.id}] ({r.effect}) {r.summary}" for r in rules]
        unclear = [r.id for r in rules if r.confidence != "settled"]
        if unclear:
            lines.append(f"Not settled by the rulebook: {', '.join(unclear)} - take the conservative path and confirm with counsel.")
        lines.append("(Offline mode: no LLM configured. This is not legal advice.)")
        return "\n".join(lines)


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())
