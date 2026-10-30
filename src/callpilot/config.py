from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="CALLPILOT_", extra="ignore")

    # Inference: any OpenAI-compatible endpoint (Nebius Token Factory, build.nvidia.com, local vLLM/NIM).
    # Un-prefixed names accepted for convenience.
    nebius_api_key: str = Field("", validation_alias=AliasChoices("NEBIUS_API_KEY", "NVIDIA_API_KEY", "CALLPILOT_API_KEY"))
    nebius_base_url: str = Field("https://api.tokenfactory.nebius.com/v1/",
                                 validation_alias=AliasChoices("NEBIUS_BASE_URL", "CALLPILOT_BASE_URL"))
    # One 3B-active hybrid-reasoning model for everything: thinking off on the live path (whispers,
    # replies, judge, briefs), thinking on for the legal reasoner. Swap slow_model for a larger
    # Nemotron on a dedicated endpoint when latency allows.
    fast_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    slow_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"

    riva_server: str = Field("", validation_alias=AliasChoices("RIVA_SERVER", "CALLPILOT_RIVA_SERVER"))

    company_name: str = "Acme Inc."
    company_address: str = ""
    callback_number: str = ""
    dncl_registered: bool = False

    rules_dir: Path = ROOT / "rules"  # legal rule KB (read-only to the agent)
    knowledge_dir: Path = ROOT / "knowledge"  # facts.md (approved claims) + docs/ (read-only)
    company_file: Path = ROOT / "company.yaml"  # who the assistant speaks for
    data_dir: Path = ROOT / "data"  # memory, audit, internal DNCL (the only rw path)


settings = Settings()
