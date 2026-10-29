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
    fast_model: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    slow_model: str = "nvidia/Nemotron-3-Ultra-550b-a55b"

    riva_server: str = Field("", validation_alias=AliasChoices("RIVA_SERVER", "CALLPILOT_RIVA_SERVER"))

    company_name: str = "Acme Inc."
    company_address: str = ""
    callback_number: str = ""
    dncl_registered: bool = False

    rules_dir: Path = ROOT / "rules"
    data_dir: Path = ROOT / "data"

    # Facts the LLM may assert about the product. Anything else is flagged by the guardrail.
    approved_claims_file: Path = ROOT / "rules" / "approved_claims.txt"


settings = Settings()
