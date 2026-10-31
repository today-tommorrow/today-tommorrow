import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_session
from app.main import app


class HealthTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_success_queries_database(self):
        session = MagicMock()
        app.dependency_overrides[get_session] = lambda: session
        response = TestClient(app).get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(str(session.execute.call_args.args[0]), "SELECT 1")

    def test_database_failure_is_sanitized(self):
        session = MagicMock()
        session.execute.side_effect = OperationalError("private-host", {}, Exception("private-password"))
        app.dependency_overrides[get_session] = lambda: session
        response = TestClient(app).get("/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "database_unavailable"})
        self.assertNotIn("private", response.text)
