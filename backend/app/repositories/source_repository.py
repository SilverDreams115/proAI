from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import SourceModel
from app.schemas.source import SourceCreate


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_sources(self) -> list[SourceModel]:
        statement = select(SourceModel).order_by(SourceModel.name.asc())
        return list(self.session.scalars(statement))

    def get_by_name(self, name: str) -> SourceModel | None:
        statement = select(SourceModel).where(SourceModel.name == name)
        return self.session.scalar(statement)

    def create_source(self, payload: SourceCreate) -> SourceModel:
        source = SourceModel(
            name=payload.name,
            base_url=str(payload.base_url),
            kind=payload.kind,
            parser_profile=payload.parser_profile,
            is_active=payload.is_active,
        )
        self.session.add(source)
        self.session.flush()
        self.session.refresh(source)
        return source
