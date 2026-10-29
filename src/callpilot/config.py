from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CALLPILOT_", extra="ignore")

    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    fast_model: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    slow_model: str = "nvidia/Nemotron-3-Ultra-550b-a55b"

    riva_server: str = ""

    company_name: str = "Acme Inc."
    company_address: str = ""
    callback_number: str = ""
    dncl_registered: bool = False

    rules_dir: Path = ROOT / "rules"
    data_dir: Path = ROOT / "data"

    # Facts the LLM may assert about the product. Anything else is flagged by the guardrail.
    approved_claims_file: Path = ROOT / "rules" / "approved_claims.txt"

    def __init__(self, **kw):
        super().__init__(**kw)
        # NEBIUS_* are not prefixed with CALLPILOT_; read them explicitly.
        import os

        self.nebius_api_key = os.getenv("NEBIUS_API_KEY", self.nebius_api_key)
        self.nebius_base_url = os.getenv("NEBIUS_BASE_URL", self.nebius_base_url)
        self.riva_server = os.getenv("RIVA_SERVER", self.riva_server)


settings = Settings()
