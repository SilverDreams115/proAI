from fastapi import APIRouter

from app.workers.scheduler_worker import get_worker_state
from app.workers.scheduler_worker import worker

router = APIRouter(prefix="/worker", tags=["worker"])


@router.post("/scheduler/run-once")
async def run_scheduler_once() -> dict[str, int]:
    return {"executed_runs": worker.run_once()}


@router.get("/scheduler/status")
async def get_scheduler_status() -> dict[str, float | int | str | None]:
    state = get_worker_state()
    return {
        "executed_runs": state.executed_runs,
        "failed_iterations": state.failed_iterations,
        "last_polled_at": state.last_polled_at,
        "last_executed_at": state.last_executed_at,
        "last_error_at": state.last_error_at,
        "last_error_message": state.last_error_message,
        "last_cycle_duration_ms": state.last_cycle_duration_ms,
    }
