class AppError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class ValidationError(AppError):
    status_code = 422


class RunDueJobsBudgetExceeded(BaseException):
    """Raised by the scheduler worker's SIGALRM handler to unblock a stalled
    source refresh (see ``scheduler_worker.RUN_DUE_JOBS_BUDGET_SECONDS``).

    Subclasses BaseException (not Exception) so it propagates past the
    ``except Exception`` guards inside ``IngestionService`` and
    ``SchedulerService.run_due_jobs`` instead of being swallowed as an
    ordinary job failure. Both of those layers catch it specifically —
    to record the interrupted run/job as failed rather than leaving it
    orphaned — and then re-raise so it still unwinds the whole batch in
    ``SchedulerWorker.run_once``.
    """
