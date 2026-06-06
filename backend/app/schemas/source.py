from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator
from pydantic import HttpUrl


class SourceCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    base_url: HttpUrl
    kind: str
    parser_profile: str = "generic"
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or len(value) > 120:
            raise ValueError("name must be between 1 and 120 characters.")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.:/")
        if any(char not in allowed for char in value):
            raise ValueError("name contains unsupported characters.")
        return value

    @field_validator("kind", "parser_profile")
    @classmethod
    def validate_slug_like_fields(cls, value: str) -> str:
        if not value:
            raise ValueError("value must not be empty.")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
        normalized = value.strip().lower()
        if any(char not in allowed for char in normalized):
            raise ValueError("value must contain only lowercase letters, digits and underscores.")
        return normalized


class SourceResponse(BaseModel):
    id: str
    name: str
    base_url: str
    kind: str
    parser_profile: str
    is_active: bool
    created_at: datetime
