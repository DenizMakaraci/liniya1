from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class CallMode(StrEnum):
    HUMAN = "human"  # human speaks, AI whispers (copilot)
    AUTONOMOUS = "autonomous"  # AI speaks


class Audience(StrEnum):
    CONSUMER = "consumer"
    BUSINESS = "business"
    UNKNOWN = "unknown"


class ConsentEvidence(BaseModel):
    """Proof the prospect agreed to be contacted (CRTC: must name the specific number)."""

    kind: str  # written | oral | electronic | callback_request | existing_business_relationship
    number: str
    captured_at: datetime
    source: str = ""  # e.g. "web form id 1234", "recording url"
    for_automated_calls: bool = False  # express ADAD consent is separate from DNCL consent


class CallContext(BaseModel):
    direction: Direction
    mode: CallMode
    prospect_number: str
    audience: Audience = Audience.UNKNOWN
    consent: ConsentEvidence | None = None
    sector: str = "general"  # general | finance | insurance | health | legal
    local_time: datetime | None = Field(default=None, description="Prospect's local time")
    purpose: str = "solicitation"  # solicitation | survey | account | emergency
    records_call: bool = True
    dncl_scrubbed_at: datetime | None = None
