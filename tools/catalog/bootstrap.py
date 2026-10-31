"""Bootstrap the pinned local education DB without a model or remote DB access."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from artifacts import (
    ARCHIVE_SHA256,
    SOURCE_COMMIT,
    archive_path,
    extract_verified,
    load_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    archive = archive_path(ROOT, args.archive)
    cache = ROOT / ".local" / "catalog"
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="verified-", dir=cache) as temporary:
        extracted = Path(temporary) / "data"
        extract_verified(archive, extracted)
        artifacts = load_artifacts(extracted)
        if args.dry_run:
            manifest = artifacts[0]
            report = {"status": "dry_run_passed", "courses": 406, "units": 6099,
                      "shape": [6099, 1024], "profile_id": manifest["embedding_profile_id"]}
        else:
            from database import execute, local_connections
            report = execute(local_connections(os.environ), artifacts, args.verify_only)
    report.update(source_commit=SOURCE_COMMIT, archive_sha256=ARCHIVE_SHA256,
                  checked_at=datetime.now(timezone.utc).isoformat())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_report.replace(args.report)
    print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate package only; no DB connection")
    mode.add_argument("--verify-only", action="store_true", help="Read-only integrity/search checks; no reimport")
    parser.add_argument("--report", type=Path, help="Write JSON only after successful completion")
    try:
        run(parser.parse_args())
    except Exception as exc:
        # Never echo driver exception messages, DSNs, environment values, or raw metadata.
        code = str(exc) if isinstance(exc, ValueError) and str(exc).replace("_", "").isalnum() else type(exc).__name__
        print(f"Catalog bootstrap failed ({code}). Connection details omitted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
