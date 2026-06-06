import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import ModelTrainingRunModel


class TrainingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_run(
        self,
        model_name: str,
        training_sample_size: int,
        artifact: dict[str, object],
    ) -> ModelTrainingRunModel:
        run = ModelTrainingRunModel(
            model_name=model_name,
            training_sample_size=training_sample_size,
            artifact_json=json.dumps(artifact, sort_keys=True),
        )
        self.session.add(run)
        self.session.flush()
        self.session.refresh(run)
        return run

    def latest_run(self, model_name: str) -> ModelTrainingRunModel | None:
        statement = (
            select(ModelTrainingRunModel)
            .where(ModelTrainingRunModel.model_name == model_name)
            .order_by(ModelTrainingRunModel.trained_at.desc())
        )
        return self.session.scalar(statement)
