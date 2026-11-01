"""Recover a killed real worker using PostgreSQL and a synthetic HTTP model."""

import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from uuid import UUID

import test_profile_drafts as profiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database_boundary import local_engine
from test_question_flow import QuestionProvider

from app.intake.question_models import IntakeQuestion
from app.jobs.models import Job


@unittest.skipUnless(
    os.environ.get("L0_DATABASE_TESTS") == "1", "requires local PostgreSQL setup"
)
class WorkerRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status.in_(["queued", "running"]))
                ),
                0,
                "worker queue must be idle for integration tests",
            )
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.profile = self.fixture.create()

    def test_restarted_worker_reclaims_killed_attempt_and_saves_one_question(self):
        entered, release, finished = Event(), Event(), Event()
        calls = []
        draft = (
            QuestionProvider()
            .generate(
                {
                    "eligible_slots": ["entry_intent"],
                    "allowed_companion_slots": {},
                    "confirmed_answers": {},
                    "resolved_unknown_slots": [],
                }
            )
            .model_dump_json()
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                calls.append(request)
                first = not entered.is_set()
                if first:
                    entered.set()
                try:
                    if first and not release.wait(timeout=15):
                        return
                    body = json.dumps(
                        {
                            "choices": [
                                {"finish_reason": "stop", "message": {"content": draft}}
                            ]
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except BrokenPipeError, ConnectionResetError:
                    pass  # The deliberately terminated worker closed its connection.
                finally:
                    if first:
                        finished.set()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.block_on_close = False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        worker = None
        try:
            env = {
                **os.environ,
                "DATABASE_URL": self.engine.url.render_as_string(hide_password=False),
                "QUESTION_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "QUESTION_MODEL_ID": QuestionProvider.model,
                "QUESTION_MODEL_TIMEOUT_SECONDS": "5",
                "WORKER_LEASE_SECONDS": "5",
                "WORKER_MAX_ATTEMPTS": "3",
            }
            with patch.dict(os.environ, env):
                accepted = self.fixture.client.post(
                    "/api/profiles/" + self.profile["id"] + "/questions",
                    headers={
                        "X-CSRF-Token": self.fixture.csrf,
                        "Idempotency-Key": "synthetic-worker-restart-key",
                    },
                    json={"expected_revision": 0},
                )
            self.assertEqual(accepted.status_code, 202)
            job_id = UUID(accepted.json()["job_id"])
            command = [sys.executable, "-m", "app.intake.worker", "--once"]
            backend = Path(__file__).resolve().parents[1]
            worker = subprocess.Popen(
                command,
                cwd=backend,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertTrue(entered.wait(timeout=10), "worker did not reach model HTTP")
            worker.terminate()
            worker.wait(timeout=5)
            self.assertNotEqual(worker.returncode, 0)

            with Session(self.engine) as db, db.begin():
                job = db.get(Job, job_id)
                self.assertIsNotNone(job)
                assert job is not None
                self.assertEqual((job.status, job.attempt), ("running", 1))
                original_token = job.lease_token
                self.assertEqual(
                    db.scalar(
                        select(func.count())
                        .select_from(IntakeQuestion)
                        .where(IntakeQuestion.job_id == job_id)
                    ),
                    0,
                )
                # Advance only this synthetic job's lease, without a timing sleep.
                job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

            release.set()
            self.assertTrue(
                finished.wait(timeout=3), "first model handler did not stop"
            )
            restarted = subprocess.run(
                command,
                cwd=backend,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            self.assertEqual(restarted.returncode, 0)
            with Session(self.engine) as db:
                job = db.get(Job, job_id)
                assert job is not None
                self.assertEqual((job.status, job.attempt), ("succeeded", 2))
                self.assertNotEqual(job.lease_token, original_token)
                questions = db.scalars(
                    select(IntakeQuestion).where(
                        IntakeQuestion.profile_id == UUID(self.profile["id"])
                    )
                ).all()
                self.assertEqual(len(questions), 1)
                self.assertEqual(questions[0].job_id, job_id)
                self.assertEqual(job.result_json, {"question_id": str(questions[0].id)})
            self.assertEqual(len(calls), 2)
            self.assertTrue(
                all(call["response_format"]["type"] == "json_schema" for call in calls)
            )
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "model HTTP server did not stop")
            if entered.is_set():
                self.assertTrue(
                    finished.wait(timeout=3), "first model handler did not stop"
                )
