"""Run with `python -m app.intake.worker`; no test provider is available by config."""

import argparse
import os
import time
from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.intake import store
from app.intake.question_config import QuestionSettings
from app.intake.question_service import save_question
from app.jobs import store as jobs


class LostLease(Exception):
    pass


def utcnow():
    return datetime.now(UTC)


def run_once(
    factory, provider, *, lease_seconds: int = 120, max_attempts: int = 3
) -> bool:
    with factory() as db, db.begin():
        claim = jobs.claim(
            db,
            now=datetime.now(UTC),
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            clock=utcnow,
        )
    if claim is None:
        return False
    stop, cancelled = Event(), Event()

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
    except Exception:
        # Provider exceptions may contain user/model content. Persist only this code.
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
