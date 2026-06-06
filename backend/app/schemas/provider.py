from pydantic import BaseModel


class ProviderCatalogResponse(BaseModel):
    provider_id: str
    connector_kind: str
    parser_profile: str
    description: str
