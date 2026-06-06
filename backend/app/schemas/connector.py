from pydantic import BaseModel


class ConnectorMetadataResponse(BaseModel):
    name: str
    kind: str
    base_url: str
    description: str
