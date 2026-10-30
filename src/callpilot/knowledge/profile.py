"""Company profile: who the assistant speaks for. Loaded from company.yaml (read-only to the agent);
CALLPILOT_COMPANY_* env vars remain as fallbacks for quick setups.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..config import ROOT, settings


class CompanyProfile(BaseModel):
    name: str = "Acme Inc."
    legal_name: str = ""
    address: str = ""
    callback_number: str = ""  # local or toll-free number given in disclosures (CRTC Part IV s.4)
    handoff_number: str = ""  # where a human picks up; defaults to callback_number
    privacy_policy_url: str = ""
    privacy_officer_email: str = ""  # Quebec Law 25 requires a designated person
    sector: str = "general"  # general | finance | insurance | health | legal
    languages: list[str] = Field(default_factory=lambda: ["en"])
    dncl_registered: bool = False
    dncl_registration_id: str = ""
    assistant_name: str = "Ada"
    default_rep: str = ""
    product: str = ""
    retention_days: int = 3 * 365
    business_hours: str = ""  # free text the assistant may quote, e.g. "Mon-Fri 9-5 ET"

    def disclosure_vars(self, rep: str = "", product: str = "") -> dict:
        return {
            "company": self.name, "address": self.address, "phone": self.callback_number,
            "dncl_registered": self.dncl_registered, "rep": rep or self.default_rep or "{rep}",
            "product": product or self.product or "{product}", "privacy_url": self.privacy_policy_url,
        }

    def as_prompt(self) -> str:
        lines = [f"Company: {self.name}" + (f" ({self.legal_name})" if self.legal_name else "")]
        if self.address:
            lines.append(f"Address: {self.address}")
        if self.callback_number:
            lines.append(f"Phone: {self.callback_number}")
        if self.business_hours:
            lines.append(f"Business hours: {self.business_hours}")
        if self.privacy_policy_url:
            lines.append(f"Privacy policy: {self.privacy_policy_url}")
        lines.append(f"Languages: {', '.join(self.languages)}")
        return "\n".join(lines)


def _env_defaults() -> dict:
    return {
        "name": settings.company_name, "address": settings.company_address,
        "callback_number": settings.callback_number, "dncl_registered": settings.dncl_registered,
    }


@lru_cache(maxsize=4)
def load_profile(path: Path | None = None) -> CompanyProfile:
    path = path or settings.company_file
    data = _env_defaults()
    if path.exists():
        data.update(yaml.safe_load(path.read_text()) or {})
    p = CompanyProfile.model_validate(data)
    if not p.handoff_number:
        p.handoff_number = p.callback_number
    return p


__all__ = ["ROOT", "CompanyProfile", "load_profile"]
