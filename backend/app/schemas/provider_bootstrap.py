from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import HttpUrl
from pydantic import field_validator


class ProviderBootstrapRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_name: str
    provider_id: str
    season_path: str | None = None
    competition_code: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    feed_url: HttpUrl | None = None
    local_path: str | None = None

    @field_validator("source_name")
    @classmethod
    def validate_source_name(cls, value: str) -> str:
        if not value or len(value) > 120:
            raise ValueError("source_name must be between 1 and 120 characters.")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.:/")
        if any(char not in allowed for char in value):
            raise ValueError("source_name contains unsupported characters.")
        return value

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if not normalized or any(char not in allowed for char in normalized):
            raise ValueError("provider_id contains unsupported characters.")
        return normalized
