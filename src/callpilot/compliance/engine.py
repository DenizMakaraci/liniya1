"""Pre-call jurisdiction gate.

Deterministic: given a CallContext, returns a CallPolicy that says whether the call may proceed,
what must be said first (ordered opening script), which obligations the pipeline must enforce
during/after the call, and which rules were applied (with citations). Anything the rulebook marks
as `confidence: unclear` is surfaced as an open question for the slow-path reasoner / a human.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .jurisdiction import Jurisdiction, detect_jurisdiction
from .models import CallContext, CallMode
from .rules import Rule, RuleBook

# Consent kinds strong enough to lift a `forbid ... unless_consent: X` rule.
CONSENT_SATISFIES = {
    "adad_express": {"written", "oral", "electronic", "callback_request"},
    "written_express": {"written", "electronic", "callback_request"},
    "express": {"written", "oral", "electronic", "callback_request"},
}


class AppliedRule(BaseModel):
    id: str
    effect: str
    summary: str
    section: str = ""
    source_title: str = ""
    source_url: str = ""
    confidence: str = "settled"


class CallPolicy(BaseModel):
    allowed: bool
    jurisdiction: str
    jurisdiction_description: str
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)  # for slow-path reasoning
    opening_script: list[str] = Field(default_factory=list)  # ordered, must be said first
    opening_disclosures: list[tuple[str, str]] = Field(default_factory=list)  # (obligation, text)
    obligations: set[str] = Field(default_factory=set)
    calling_window: str | None = None
    applied_rules: list[AppliedRule] = Field(default_factory=list)

    def requires(self, obligation: str) -> bool:
        return obligation in self.obligations


class PolicyEngine:
    def __init__(self, rulebook: RuleBook, company: dict[str, str], is_internally_blocked=None):
        self.rulebook = rulebook
        self.company = company  # keys: company, address, phone, rep, product
        self.is_internally_blocked = is_internally_blocked  # callable(number) -> bool

    def evaluate(self, ctx: CallContext, jurisdiction: Jurisdiction | None = None) -> CallPolicy:
        j = jurisdiction or detect_jurisdiction(ctx.prospect_number)
        rules = [r for r in self.rulebook.for_jurisdiction(j.rule_code) if r.matches(ctx)]
        policy = CallPolicy(allowed=True, jurisdiction=j.rule_code, jurisdiction_description=j.description)
        disclosures: dict[str, str] = {}  # obligation -> text, de-duplicated across jurisdictions

        if (ctx.direction == "outbound" and self.is_internally_blocked
                and self.is_internally_blocked(ctx.prospect_number)):
            policy.blocking_reasons.append(
                "[default.optout_honour] prospect previously asked not to be contacted (internal DNCL)")

        for r in rules:
            policy.applied_rules.append(self._cite(r))
            if r.confidence == "unclear":
                policy.open_questions.append(f"[{r.id}] {r.summary}")
            if r.effect == "forbid":
                self._apply_forbid(r, ctx, policy)
            elif r.effect == "require":
                self._apply_require(r, ctx, policy, disclosures)
            elif r.effect == "restrict":
                self._apply_restrict(r, ctx, j, policy, disclosures)
            elif r.effect == "advise":
                if r.obligation:
                    policy.obligations.add(r.obligation)
                if r.confidence != "unclear":
                    policy.warnings.append(f"[{r.id}] {r.summary}")

        policy.opening_disclosures = self._order_script(disclosures)
        policy.opening_script = [text for _, text in policy.opening_disclosures]
        policy.allowed = not policy.blocking_reasons
        return policy

    # -- effects -----------------------------------------------------------------------------

    def _apply_forbid(self, r: Rule, ctx: CallContext, policy: CallPolicy):
        if r.unless_consent and ctx.consent:
            ok_kinds = CONSENT_SATISFIES.get(r.unless_consent, set())
            same_number = _same_number(ctx.consent.number, ctx.prospect_number)
            needs_automated = r.unless_consent in ("adad_express", "written_express")
            if (
                ctx.consent.kind in ok_kinds
                and same_number
                and (not needs_automated or ctx.consent.for_automated_calls
                     or ctx.consent.kind == "callback_request")
            ):
                policy.warnings.append(
                    f"[{r.id}] permitted only because of {ctx.consent.kind} consent "
                    f"captured {ctx.consent.captured_at:%Y-%m-%d} ({ctx.consent.source or 'no source'})"
                )
                policy.obligations.add("log_consent_evidence")
                return
        policy.blocking_reasons.append(f"[{r.id}] {r.summary}")

    def _apply_require(self, r: Rule, ctx: CallContext, policy: CallPolicy, disclosures: dict):
        if r.obligation:
            policy.obligations.add(r.obligation)
            if r.obligation == "recording_notice" and not ctx.records_call:
                return
            if r.obligation == "dncl_registration" and not self.company.get("dncl_registered"):
                policy.blocking_reasons.append(f"[{r.id}] company is not registered with the National DNCL")
            if r.obligation == "dncl_scrub_31d" and not ctx.consent:
                if ctx.dncl_scrubbed_at is None:
                    policy.blocking_reasons.append(f"[{r.id}] number has not been scrubbed against the DNCL")
                elif datetime.now(ctx.dncl_scrubbed_at.tzinfo) - ctx.dncl_scrubbed_at > timedelta(days=31):
                    policy.blocking_reasons.append(f"[{r.id}] DNCL scrub is older than 31 days")
        if r.disclosure and r.obligation:
            # Prefer the most specific (first-seen) wording for each obligation.
            disclosures.setdefault(r.obligation, r.disclosure.format_map(_Safe(self.company)))

    def _apply_restrict(self, r: Rule, ctx: CallContext, j: Jurisdiction, policy: CallPolicy,
                        disclosures: dict):
        if r.obligation:
            policy.obligations.add(r.obligation)
            if r.disclosure:
                disclosures.setdefault(r.obligation, r.disclosure.format_map(_Safe(self.company)))
        if r.hours:
            local = ctx.local_time or _now_in(j)
            window = r.hours.weekend if local.weekday() >= 5 else r.hours.weekday
            policy.calling_window = f"{window[0]}-{window[1]} ({local.tzinfo})"
            if not (_hm(window[0]) <= local.strftime("%H:%M") <= _hm(window[1])):
                policy.blocking_reasons.append(
                    f"[{r.id}] outside calling hours: local time {local:%a %H:%M}, allowed {window[0]}-{window[1]}"
                )

    # -- helpers -----------------------------------------------------------------------------

    def _cite(self, r: Rule) -> AppliedRule:
        src = self.rulebook.sources.get(r.source)
        return AppliedRule(
            id=r.id, effect=r.effect, summary=r.summary, section=r.section, confidence=r.confidence,
            source_title=src.title if src else r.source, source_url=src.url if src else "",
        )

    @staticmethod
    def _order_script(disclosures: dict[str, str]) -> list[tuple[str, str]]:
        order = [
            "identify_caller_full", "identify_caller", "ai_disclosure", "recording_notice",
            "purpose_notice", "automated_decision_notice", "no_regulated_advice",
        ]
        ranked = sorted(disclosures.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
        # identify_caller_full supersedes identify_caller
        if "identify_caller_full" in disclosures:
            ranked = [kv for kv in ranked if kv[0] != "identify_caller"]
        return ranked


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _hm(s: str) -> str:
    return s if len(s) == 5 else "0" + s


def _now_in(j: Jurisdiction) -> datetime:
    tz = ZoneInfo(j.timezones[0]) if j.timezones else None
    return datetime.now(tz)


def _same_number(a: str, b: str) -> bool:
    import phonenumbers

    try:
        return phonenumbers.parse(a, "CA") == phonenumbers.parse(b, "CA")
    except phonenumbers.NumberParseException:
        return a == b


def make_engine(settings, rulebook: RuleBook, rep: str = "", product: str = "") -> PolicyEngine:
    from .audit import AuditLog

    return PolicyEngine(
        rulebook,
        {
            "company": settings.company_name,
            "address": settings.company_address,
            "phone": settings.callback_number,
            "dncl_registered": settings.dncl_registered,
            "rep": rep or "{rep}",
            "product": product or "{product}",
        },
        is_internally_blocked=AuditLog(settings.data_dir, "_gate").is_internally_blocked,
    )


__all__ = ["AppliedRule", "CallMode", "CallPolicy", "PolicyEngine", "make_engine"]
