from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str
    portguard_model: str = "claude-opus-4-6"
    portguard_max_tokens: int = 4096

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
