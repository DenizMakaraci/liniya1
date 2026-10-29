from dataclasses import dataclass

import phonenumbers
from phonenumbers import PhoneNumberType, geocoder, timezone

EU_EEA = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO",
}

# Quebec NPAs (area codes). Everything else in CA falls back to federal rules.
QUEBEC_AREA_CODES = {"263", "354", "367", "418", "438", "450", "468", "514", "579", "581", "819", "873"}


@dataclass(frozen=True)
class Jurisdiction:
    country: str  # ISO-3166 alpha-2, e.g. "CA"
    region: str | None  # e.g. "QC"
    rule_code: str  # key into the rulebook: "CA-QC", "CA", "US", "EU", "DEFAULT"
    timezones: tuple[str, ...]
    is_mobile: bool
    description: str


def detect_jurisdiction(number: str, default_region: str = "CA") -> Jurisdiction:
    parsed = phonenumbers.parse(number, default_region)
    country = phonenumbers.region_code_for_number(parsed) or default_region
    region = None
    if country == "CA":
        npa = str(parsed.national_number)[:3]
        if npa in QUEBEC_AREA_CODES:
            region = "QC"
    if region:
        code = f"{country}-{region}"
    elif country in ("CA", "US"):
        code = country
    elif country in EU_EEA:
        code = "EU"
    else:
        code = "DEFAULT"
    ntype = phonenumbers.number_type(parsed)
    return Jurisdiction(
        country=country,
        region=region,
        rule_code=code,
        timezones=tuple(timezone.time_zones_for_number(parsed)),
        is_mobile=ntype in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE),
        description=geocoder.description_for_number(parsed, "en") or country,
    )
