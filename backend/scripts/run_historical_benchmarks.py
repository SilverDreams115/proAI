from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session as db_session
from app.db.migrations import run_migrations
from app.models import tables  # noqa: F401
from app.repositories.entity_repository import EntityRepository
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.source_repository import SourceRepository
from app.repositories.training_repository import TrainingRepository
from app.schemas.provider_bootstrap import ProviderBootstrapRequest
from app.services.history_import_service import HistoryImportService
from app.services.model_training_service import ModelTrainingService
from app.services.source_service import SourceService


DATASETS = [
    ("epl_2324", "Historical EPL 2023-24", "mmz4281/2324/E0.csv"),
    ("epl_2425", "Historical EPL 2024-25", "mmz4281/2425/E0.csv"),
    ("laliga_2425", "Historical LaLiga 2024-25", "mmz4281/2425/SP1.csv"),
    ("seriea_2425", "Historical Serie A 2024-25", "mmz4281/2425/I1.csv"),
    ("bundesliga_2425", "Historical Bundesliga 2024-25", "mmz4281/2425/D1.csv"),
]


def evaluate_database(db_path: Path) -> dict[str, object]:
    db_session.configure_session(f"sqlite:///{db_path}")
    session = db_session.SessionLocal()
    try:
        service = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        return service.evaluate_walk_forward(min_training_matches=12, confidence_threshold=0.5)
    finally:
        session.close()


def build_dataset(db_path: Path, source_name: str, season_path: str) -> dict[str, object]:
    if db_path.exists():
        db_path.unlink()
    db_session.configure_session(f"sqlite:///{db_path}")
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        source = SourceService(SourceRepository(session)).create_source_from_provider(
            ProviderBootstrapRequest(
                source_name=source_name,
                provider_id="football-data-uk-season-csv",
                season_path=season_path,
            )
        )
        run = HistoryImportService(IngestionRepository(session)).import_source_history(source.id)
        evaluation = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        ).evaluate_walk_forward(min_training_matches=12, confidence_threshold=0.5)
        return {
            "db": str(db_path),
            "source_name": source_name,
            "season_path": season_path,
            "import_status": run.status,
            "documents_found": run.documents_found,
            "evaluation": evaluation,
        }
    finally:
        session.close()


def main() -> None:
    base_dir = Path("backend/data/eval_runs")
    base_dir.mkdir(parents=True, exist_ok=True)

    per_dataset: list[dict[str, object]] = []
    combined_db = base_dir / "combined_top_leagues.db"
    if combined_db.exists():
        combined_db.unlink()
    db_session.configure_session(f"sqlite:///{combined_db}")
    run_migrations(db_session.engine)
    combined_session = db_session.SessionLocal()
    try:
        source_service = SourceService(SourceRepository(combined_session))
        history_service = HistoryImportService(IngestionRepository(combined_session))
        for slug, source_name, season_path in DATASETS:
            db_path = base_dir / f"{slug}.db"
            per_dataset.append(build_dataset(db_path, source_name, season_path))
            combined_source = source_service.create_source_from_provider(
                ProviderBootstrapRequest(
                    source_name=f"{source_name} combined",
                    provider_id="football-data-uk-season-csv",
                    season_path=season_path,
                )
            )
            history_service.import_source_history(combined_source.id)

        combined_evaluation = ModelTrainingService(
            TrainingRepository(combined_session),
            EntityRepository(combined_session),
            ResultRepository(combined_session),
        ).evaluate_walk_forward(min_training_matches=12, confidence_threshold=0.5)
    finally:
        combined_session.close()

    payload = {
        "datasets": per_dataset,
        "combined": {
            "db": str(combined_db),
            "evaluation": combined_evaluation,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
