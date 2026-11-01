"""Run with `python -m app.intake.worker`; no test provider is available by config."""

import argparse
import logging
import os
import time
from datetime import UTC, datetime
from threading import Event, Thread

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.ai.questions import QuestionError
from app.db import SessionLocal
from app.intake import store
from app.intake.question_config import QuestionSettings
from app.intake.question_service import save_question
from app.jobs import store as jobs
from app.jobs.execution import execution_slot

LOGGER = logging.getLogger(__name__)
FAILURE_CODES = frozenset(
    {
        "generation_cancelled",
        "invalid_generation_context",
        "invalid_model_response",
        "model_http_error",
        "model_response_too_large",
        "model_timeout",
        "model_unavailable",
        "duplicate_or_empty_question_option",
        "duplicate_question_patch",
        "empty_question",
        "forbidden_question_content",
        "forbidden_question_patch",
        "inconsistent_budget_patch",
        "ineligible_question_target",
        "invalid_question_json",
        "invalid_question_patch",
        "missing_budget_amount",
        "unresolved_question_target",
    }
)


def failure_category(error: Exception) -> str:
    """Return only known fixed codes; never format provider or validation content."""
    if isinstance(error, QuestionError):
        if (
            len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in FAILURE_CODES
        ):
            return error.args[0]
        return "unexpected_error"
    if isinstance(error, SQLAlchemyError):
        return "database_error"
    if isinstance(error, ValidationError):
        return "validation_error"
    if (
        type(error) is ValueError
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] == "provider_model_changed"
    ):
        return "provider_model_changed"
    return "unexpected_error"


class LostLease(Exception):
    pass


def utcnow():
    return datetime.now(UTC)


def run_once(
    factory, provider, *, lease_seconds: int = 120, max_attempts: int = 3
) -> bool:
    with execution_slot(factory) as acquired:
        if not acquired:
            return False
        return _run_once(
            factory, provider, lease_seconds=lease_seconds, max_attempts=max_attempts
        )


def _run_once(
    factory, provider, *, lease_seconds: int = 120, max_attempts: int = 3
) -> bool:
    with factory() as db, db.begin():
        claim = jobs.claim(
            db,
            now=datetime.now(UTC),
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            clock=utcnow,
            kind="intake_question",
        )
    if claim is None:
        return False
    stop, cancelled = Event(), Event()
    stage = "provider_generation"

    def heartbeat():
        while not stop.wait(max(0.25, lease_seconds / 3)):
            try:
                with factory() as db, db.begin():
                    if not jobs.heartbeat(
                        db,
                        claim,
                        now=datetime.now(UTC),
                        lease_seconds=lease_seconds,
                        clock=utcnow,
                    ):
                        cancelled.set()
                        return
            except SQLAlchemyError:
                cancelled.set()
                return

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if provider.model != claim.input_json["model_id"]:
            raise ValueError("provider_model_changed")
        draft = provider.generate(claim.input_json["context"], cancelled=cancelled)
        stop.set()
        thread.join(timeout=5)
        stage = "question_persistence"
        with factory() as db, db.begin():
            profile = store.get_profile(db, claim.profile_id, claim.user_id, lock=True)
            if jobs.owns(db, claim, now=datetime.now(UTC), clock=utcnow) is None:
                return True
            if profile.revision != claim.profile_revision:
                from app.intake.question_service import invalidate

                invalidate(db, profile.id)
                return True
            question_id = save_question(db, claim, draft)
            if not jobs.complete(
                db,
                claim,
                now=datetime.now(UTC),
                result_json={"question_id": str(question_id)},
                clock=utcnow,
            ):
                raise LostLease()
    except LostLease:
        pass  # The whole result transaction rolled back; a new owner may finish.
    except Exception as error:
        # Only fixed categories leave the worker; public job errors stay generic.
        LOGGER.warning(
            "question_generation_failed job_id=%s stage=%s category=%s",
            claim.id,
            stage,
            failure_category(error),
        )
        try:
            with factory() as db, db.begin():
                jobs.fail(
                    db,
                    claim,
                    now=datetime.now(UTC),
                    code="question_generation_failed",
                    clock=utcnow,
                )
        except SQLAlchemyError:
            pass  # Lease expiry makes a crashed/disconnected attempt recoverable.
    finally:
        stop.set()
        thread.join(timeout=5)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="저장된 질문 작업을 실행합니다.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        provider = QuestionSettings.load().provider()
        lease = int(os.environ.get("WORKER_LEASE_SECONDS", "120"))
        attempts = int(os.environ.get("WORKER_MAX_ATTEMPTS", "3"))
        poll = float(os.environ.get("WORKER_POLL_SECONDS", "1"))
        if not (5 <= lease <= 3600 and 1 <= attempts <= 10 and 0.1 <= poll <= 60):
            raise ValueError()
    except ValueError:
        print("질문 모델과 Worker 설정을 확인해주세요.")
        return 1
    try:
        while True:
            try:
                worked = run_once(
                    SessionLocal, provider, lease_seconds=lease, max_attempts=attempts
                )
            except SQLAlchemyError:
                print("작업 DB에 연결할 수 없습니다.")
                if args.once:
                    return 1
                worked = False
            if args.once:
                return 0
            if not worked:
                time.sleep(poll)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
