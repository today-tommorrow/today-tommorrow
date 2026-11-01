"""Explicit live-BGE evaluation command; imports no application routes or workers."""

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.ai.embeddings import BgeM3Embeddings, EmbeddingError

from .contracts import CatalogError
from .evaluation import (
    EvaluationError,
    evaluate_dataset,
    load_dataset,
    render_report,
    validate_evaluation_options,
)
from .repository import REQUIRED_CONFIG, CatalogRepository
from .service import SearchService


def environment_metadata(engine: Engine) -> dict[str, object]:
    """Version facts only: no hostnames, endpoints, environment values or credentials."""
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        revision, dirty = "unavailable", None
    packages: dict[str, str] = {}
    for name in ("sqlalchemy", "psycopg"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        database_version = connection.exec_driver_sql("SHOW server_version").scalar_one()
        vector_version = connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
    return {
        "code_commit": revision, "code_worktree_dirty": dirty,
        "python": platform.python_version(), "python_implementation": platform.python_implementation(),
        "os": platform.system(), "os_release": platform.release(), "architecture": platform.machine(),
        "packages": packages, "postgresql": database_version, "pgvector": vector_version,
        "query_runtime": {"adapter": "BgeM3Embeddings", "service_profile": "bge-m3-flagembedding-cuda-v1",
                          "device": "cuda:0", "dtype": "torch.float16",
                          "verification": "adapter validates model metadata before each query; errors are recorded as failed cases"},
        "required_embedding_config": REQUIRED_CONFIG,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--limit", default=30, type=int)
    parser.add_argument("--evidence-limit", default=3, type=int)
    parser.add_argument("--k", required=True, type=int, nargs="+")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-report", required=True, type=Path)
    args = parser.parse_args()
    engine = None
    try:
        # Resolve and validate the fixed expectations before any external call.
        dataset = load_dataset(args.dataset)
        validate_evaluation_options(dataset, tuple(args.k), args.limit, args.evidence_limit)
        if (args.profile_id, args.dataset_id) != (dataset.profile_id, dataset.dataset_id):
            raise EvaluationError("evaluation_catalog_mismatch")
        paths = [args.dataset.resolve(), args.output_json.resolve(), args.output_report.resolve()]
        if len(set(paths)) != len(paths):
            raise EvaluationError("evaluation_output_path_conflict")
        if args.output_json.exists() or args.output_report.exists():
            raise EvaluationError("evaluation_output_already_exists")
        if not all(path.parent.is_dir() for path in (args.output_json, args.output_report)):
            raise EvaluationError("evaluation_output_directory_required")
        if not os.environ.get("DATABASE_URL"):
            raise EvaluationError("database_configuration_required")
        provider = BgeM3Embeddings(args.base_url, timeout=args.timeout)
        engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
        repository = CatalogRepository(engine, profile_id=args.profile_id, dataset_id=args.dataset_id)
        snapshot = repository.prepare()
        environment = environment_metadata(engine)
        environment["request_timeout_seconds"] = args.timeout
        report = evaluate_dataset(
            dataset, SearchService(repository, provider), snapshot,
            cutoffs=tuple(args.k), limit=args.limit, evidence_limit=args.evidence_limit,
            execution_mode="live_bge_cuda", environment=environment,
        )
        with args.output_json.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        with args.output_report.open("x", encoding="utf-8") as output:
            output.write(render_report(report))
        failed = bool(report["summary"]["failed_cases"])
        print(json.dumps({"status": "failed" if failed else "ok", "summary": report["summary"]}, ensure_ascii=False, allow_nan=False))
        return 1 if failed else 0
    except (EvaluationError, CatalogError, EmbeddingError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    except (OSError, ValueError, TypeError, SQLAlchemyError):
        print(json.dumps({"status": "failed", "error": "evaluation_execution_failed"}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
