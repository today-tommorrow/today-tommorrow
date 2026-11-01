"""Seed or clean synthetic sessions in the existing guarded local test database.
Run with backend/.venv/bin/python; prints no credentials. Keep output outside Git.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cleanup", action="store_true")
args = parser.parse_args()
root = args.root.resolve()
sys.path[:0] = [
    str(root / "scripts"),
    str(root / "backend"),
    str(root / "backend/tests"),
]
from local import load_environment

os.environ.update(load_environment(root / ".env"))
from app.auth.config import AuthSettings
from app.auth.models import User
from app.auth.store import AuthStore
from app.saved_courses.catalog import read_catalog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

engine = local_engine("DATABASE_URL", "today_app")
try:
    if args.cleanup:
        value = json.loads(args.output.read_text())
        ids = [UUID(value[key]["user_id"]) for key in ("owner", "other")]
        with Session(engine) as db, db.begin():
            rows = db.scalars(select(User).where(User.id.in_(ids))).all()
            if any(not user.google_sub.startswith("synthetic-saved-") for user in rows):
                raise ValueError("refusing to delete non-synthetic user")
            db.execute(delete(User).where(User.id.in_(ids)))
        args.output.unlink()
        print("Synthetic saved-course users cleaned; session file removed.")
    else:
        if args.output.exists():
            raise ValueError("output path already exists")
        config = AuthSettings.load()
        catalog = read_catalog(engine)

        def login():
            now = datetime.now(UTC)
            with Session(engine) as db, db.begin():
                store = AuthStore(db, signing_key=config.signing_key)
                user = store.save_google_identity(
                    subject="synthetic-saved-ui-" + uuid4().hex,
                    email="synthetic@example.invalid",
                    now=now,
                )
                issued = store.issue_session(
                    user, now=now, expires_at=now + timedelta(hours=2)
                )
            return {
                "user_id": str(issued.user_id),
                "token": issued.token,
                "csrf_token": issued.csrf_token,
            }

        value = {
            "api_base_url": os.environ.get(
                "API_TARGET", "http://127.0.0.1:8000"
            ).rstrip("/")
            + "/api",
            "origin": config.origin,
            "cookie_name": "today_tommorrow_session",
            "owner": login(),
            "other": login(),
            "courses": [
                course.summary.model_dump(mode="json")
                for course in list(catalog.courses.values())[:5]
            ],
            "catalog_version_id": str(catalog.version),
        }
        # Exclusive creation keeps a reused path from exposing credentials.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(
            "Synthetic sessions and public course fixtures written to the private output file."
        )
finally:
    engine.dispose()
