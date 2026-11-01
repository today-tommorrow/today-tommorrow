"""Execute submitted recommendations with leased ownership and atomic result storage."""

from __future__ import annotations

import argparse
import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from threading import Event, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal, engine
from app.intake import store
from app.jobs import store as jobs
from app.jobs.execution import execution_slot
from app.recommendations.models import RecommendationRun

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from app.recommendations.contracts import RecommendationRunner

PHASES = frozenset(
    {
        "freezing",
        "profiling",
        "mapping_needs",
        "retrieving",
        "checking_constraints",
        "assessing",
        "ranking",
        "explaining",
        "validating",
        "persisting",
    }
)


class LostLease(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def run_once(
    factory: Callable[[], Session],
    runner: RecommendationRunner,
    *,
    lease_seconds: int = 120,
    max_attempts: int = 3,
) -> bool:
    with execution_slot(factory) as acquired:
        if not acquired:
            return False
        return _run_once(
            factory, runner, lease_seconds=lease_seconds, max_attempts=max_attempts
        )


def _run_once(
    factory: Callable[[], Session],
    runner: RecommendationRunner,
    *,
    lease_seconds: int = 120,
    max_attempts: int = 3,
) -> bool:
    with factory() as db, db.begin():
        claim = jobs.claim(
            db,
            now=utcnow(),
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            clock=utcnow,
            kind="recommendation",
        )
    if claim is None:
        return False
    stop, cancelled = Event(), Event()

    def heartbeat() -> None:
        while not stop.wait(max(0.25, lease_seconds / 3)):
            try:
                with factory() as db, db.begin():
                    if not jobs.heartbeat(
                        db,
                        claim,
                        now=utcnow(),
                        lease_seconds=lease_seconds,
                        clock=utcnow,
                    ):
                        cancelled.set()
                        return
            except SQLAlchemyError:
                cancelled.set()
                return

    def report_phase(phase: str) -> None:
        if phase not in PHASES:
            raise ValueError("invalid_recommendation_phase")
        if cancelled.is_set():
            raise LostLease()
        with factory() as db, db.begin():
            row = jobs.owns(db, claim, now=utcnow(), clock=utcnow)
            if row is None:
                cancelled.set()
                raise LostLease()
            row.phase = phase

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        from app.recommendations.contracts import validate_result

        result = validate_result(runner.run(deepcopy(claim.input_json), report_phase))
        if cancelled.is_set():
            raise LostLease()
        report_phase("persisting")
        stop.set()
        thread.join(timeout=5)
        with factory() as db, db.begin():
            profile = store.get_profile(db, claim.profile_id, claim.user_id, lock=True)
            if jobs.owns(db, claim, now=utcnow(), clock=utcnow) is None:
                return True
            if (
                profile.revision != claim.profile_revision
                or profile.status != "submitted"
                or profile.submitted_revision != claim.profile_revision
            ):
                from app.intake.question_service import invalidate

                invalidate(db, profile.id)
                return True
            run_id, created_at = uuid4(), utcnow()
            provenance = result.pop("_snapshot", {})
            public = {
                **result,
                "id": str(run_id),
                "profile_id": str(claim.profile_id),
                "profile_revision": claim.profile_revision,
                "profile_snapshot": deepcopy(claim.input_json["answers"]),
                "created_at": created_at.isoformat(),
            }
            db.add(
                RecommendationRun(
                    id=run_id,
                    job_id=claim.id,
                    profile_id=claim.profile_id,
                    user_id=claim.user_id,
                    profile_revision=claim.profile_revision,
                    input_json=deepcopy(claim.input_json),
                    public_json=public,
                    provenance_json=provenance,
                    created_at=created_at,
                )
            )
            db.flush()
            stages = [
                stage["stage"]
                for stage in result["stages"]
                if stage["status"] == "succeeded"
            ]
            if not jobs.complete(
                db,
                claim,
                now=utcnow(),
                clock=utcnow,
                result_json={
                    "recommendation_run_id": str(run_id),
                    "completed_stages": stages,
                },
            ):
                raise LostLease()
    except LostLease:
        pass
    except Exception:
        # Provider/DB failures can contain user text or secrets: only persist a fixed code.
        try:
            with factory() as db, db.begin():
                jobs.fail(
                    db, claim, now=utcnow(), code="recommendation_failed", clock=utcnow
                )
        except SQLAlchemyError:
            pass
    finally:
        stop.set()
        thread.join(timeout=5)
    return True


def main() -> int:
    from app.recommendations.settings import build_runner

    parser = argparse.ArgumentParser(description="제출한 추천 작업을 처리합니다.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        runner = build_runner(engine)
        lease = int(os.environ.get("WORKER_LEASE_SECONDS", "120"))
        attempts = int(os.environ.get("WORKER_MAX_ATTEMPTS", "3"))
        poll = float(os.environ.get("WORKER_POLL_SECONDS", "1"))
        if not (5 <= lease <= 3600 and 1 <= attempts <= 10 and 0.1 <= poll <= 60):
            raise ValueError()
    except ValueError:
        print("추천 모델과 Worker 설정을 확인해주세요.")
        return 1
    try:
        while True:
            try:
                worked = run_once(
                    SessionLocal, runner, lease_seconds=lease, max_attempts=attempts
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
