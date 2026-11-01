"""Explicit real-model retrieval probe. Never imported by the public API."""

import argparse
import json
import os
import sys
import time
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from .contracts import CatalogError, SearchFilters, json_value
from .repository import CatalogRepository
from .service import SearchService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--evidence-limit", type=int, default=3)
    parser.add_argument("--delivery-mode", choices=["online", "offline", "hybrid", "any", "unknown"])
    parser.add_argument("--start-from", type=date.fromisoformat)
    parser.add_argument("--start-to", type=date.fromisoformat)
    parser.add_argument("--listed-region")
    parser.add_argument("--budget-krw", type=int)
    parser.add_argument("--availability", choices=["weekday_day", "evening_weekend", "flexible", "unknown"])
    parser.add_argument("--field-code")
    parser.add_argument("--include-candidates", action="store_true", help="Print public source metadata/evidence; query text is never printed.")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"status": "failed", "error": "database_configuration_required"}))
        return 1
    # The standalone adapter is supplied separately; no GPU dependencies are imported.
    from app.ai.embeddings import BgeM3Embeddings, EmbeddingError

    engine = None
    started = time.monotonic()
    try:
        engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
        repository = CatalogRepository(engine, profile_id=args.profile_id, dataset_id=args.dataset_id)
        provider = BgeM3Embeddings(args.base_url, timeout=args.timeout)
        filters = SearchFilters(delivery_mode=args.delivery_mode, start_from=args.start_from,
                                start_to=args.start_to, listed_region=args.listed_region,
                                budget_krw=args.budget_krw, availability=args.availability,
                                field_code=args.field_code)
        result = SearchService(repository, provider).search(args.query, as_of=args.as_of,
                  filters=filters, limit=args.limit, evidence_limit=args.evidence_limit)
        report = json_value(result)
        if not args.include_candidates:
            report["candidate_count"] = len(result.candidates)
            report.pop("candidates")
        report.update(status="ok", elapsed_seconds=round(time.monotonic() - started, 3))
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0
    except (CatalogError, EmbeddingError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    except (ValueError, UnicodeError, SQLAlchemyError):
        print(json.dumps({"status": "failed", "error": "invalid_probe_configuration"}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
