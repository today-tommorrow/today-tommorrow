"""Explicitly upgrade an existing pinned catalog; the default is a read-only plan."""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import psycopg
from artifacts import (
    ARCHIVE_SHA256,
    extract_verified,
    file_hash,
    load_artifacts,
    require,
    value_hash,
)
from database import UNIT_FIELDS, vector_array
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

BASE_TABLES = ("embedding_profile", "dataset_snapshot", "course", "course_unit")
SNAPSHOT_TABLES = ("snapshot_course", "snapshot_unit", "catalog_batch", "active_catalog", "catalog_activation")
SNAPSHOT_INDEX = "snapshot_unit_embedding_hnsw"
# PostgreSQL 16 catalog signature of the reviewed shared DDL. The accompanying
# DDL digest and disposable-DB tests make changes to this contract explicit.
SNAPSHOT_DDL_SHA256 = "9c4ac91187a765c12cfb27ac6b366e6e2afd22c95e2a1202ee8f5879e2e63d90"
SNAPSHOT_CONTRACT_SHA256 = "21ee7b63ba3ba1a3eb9588f8fe73447167bf5514b19531e81175559217ffd0d7"


@dataclass(frozen=True)
class Expected:
    database: str
    user: str
    profile_id: str
    dataset_id: str
    reader_role: str

    def validate(self) -> None:
        require(all(isinstance(value, str) and value for value in
                    (self.database, self.user, self.reader_role)), "upgrade_identity_required")
        require(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in
                    (self.profile_id, self.dataset_id)), "invalid_upgrade_identity")
        require(self.user != self.reader_role, "upgrade_reader_is_owner")


def connection_parameters(env: dict[str, str], expected: Expected) -> dict[str, str]:
    """Require an explicit target and suppress ambient libpq routing overrides."""
    expected.validate()
    require(not any(value for key, value in env.items() if key.startswith("PG")), "upgrade_libpq_override")
    try:
        params = conninfo_to_dict(env["CATALOG_UPGRADE_DATABASE_URL"])
        allowed = {"host", "port", "dbname", "user", "password", "sslmode", "sslrootcert", "sslcert", "sslkey"}
        require(set(params) <= allowed, "invalid_upgrade_target")
        require(bool(params.get("host")) and "," not in params["host"], "invalid_upgrade_target")
        require(params.get("dbname") == expected.database and params.get("user") == expected.user,
                "upgrade_target_identity_mismatch")
        require(params["host"].startswith("/") or bool(params.get("password")), "upgrade_password_required")
        port = int(params.get("port", "5432"))
        require(1 <= port <= 65535, "invalid_upgrade_target")
        params.update(port=str(port), connect_timeout="15", options="")
        return params
    except (KeyError, TypeError, psycopg.Error):
        raise ValueError("invalid_upgrade_target") from None


def attest(conn: psycopg.Connection, expected: Expected) -> None:
    expected.validate()
    row = conn.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int").fetchone()
    require(row[:2] == (expected.database, expected.user), "upgrade_database_identity_mismatch")
    require(160000 <= row[2] < 170000, "postgresql_16_required")
    require(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone() == ("0.6.0",),
            "pgvector_060_required")
    owner = conn.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='work24_bge'").fetchone()
    require(owner == (expected.user,), "upgrade_schema_owner_mismatch")
    role = conn.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin
        FROM pg_roles WHERE rolname=%s""", (expected.reader_role,)).fetchone()
    require(role is not None and not any(role[:5]) and role[5], "upgrade_reader_privileges_mismatch")
    require(conn.execute("""SELECT 1 FROM pg_auth_members
        WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s) LIMIT 1""", (expected.reader_role,)).fetchone() is None,
        "upgrade_reader_membership_mismatch")
    require(conn.execute("SELECT has_schema_privilege(%s,'work24_bge','USAGE'),has_schema_privilege(%s,'work24_bge','CREATE')",
                         (expected.reader_role, expected.reader_role)).fetchone() == (True, False),
            "upgrade_reader_schema_mismatch")
    for name in BASE_TABLES:
        require(conn.execute("SELECT has_table_privilege(%s,%s,'SELECT'),has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                             (expected.reader_role, f"work24_bge.{name}", expected.reader_role, f"work24_bge.{name}")).fetchone() == (True, False),
                "upgrade_reader_base_permissions_mismatch")


def snapshot_state(conn: psycopg.Connection) -> str:
    names = (*SNAPSHOT_TABLES, SNAPSHOT_INDEX)
    rows = dict(conn.execute("""SELECT c.relname,c.relkind FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='work24_bge' AND c.relname=ANY(%s)""",
                             (list(names),)).fetchall())
    if not rows:
        return "legacy"
    require(set(rows) == set(names), "partial_catalog_upgrade")
    require(all(rows[name] == "r" for name in SNAPSHOT_TABLES) and rows[SNAPSHOT_INDEX] == "i",
            "catalog_upgrade_relation_mismatch")
    require(conn.execute("""SELECT i.indisvalid,am.amname FROM pg_index i
        JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_am am ON am.oid=c.relam WHERE n.nspname='work24_bge' AND c.relname=%s""",
                         (SNAPSHOT_INDEX,)).fetchone() == (True, "hnsw"), "catalog_upgrade_index_mismatch")
    require(value_hash(schema_contract(conn)) == SNAPSHOT_CONTRACT_SHA256, "catalog_upgrade_schema_mismatch")
    return "upgraded"


def schema_contract(conn: psycopg.Connection) -> dict:
    """Compare PostgreSQL metadata, never parse or execute untrusted DDL."""
    contract = {}
    for table in SNAPSHOT_TABLES:
        relation = f"work24_bge.{table}"
        contract[table] = {
            "columns": conn.execute("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid)
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (relation,)).fetchall(),
            "constraints": conn.execute("""SELECT contype,pg_get_constraintdef(oid),convalidated,condeferrable,condeferred
                FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY contype,pg_get_constraintdef(oid)""", (relation,)).fetchall(),
            "indexes": conn.execute("""SELECT pg_get_indexdef(indexrelid),indisvalid,indisready
                FROM pg_index WHERE indrelid=%s::regclass ORDER BY pg_get_indexdef(indexrelid)""", (relation,)).fetchall(),
            "policies": conn.execute("""SELECT relrowsecurity,relforcerowsecurity,relpersistence,reloptions,
                (SELECT count(*) FROM pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal)
                FROM pg_class c WHERE oid=%s::regclass""", (relation,)).fetchone(),
        }
    return contract


def verify_snapshot_permissions(conn: psycopg.Connection, expected: Expected) -> None:
    for name in SNAPSHOT_TABLES:
        relation = f"work24_bge.{name}"
        require(conn.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=%s::regclass", (relation,)).fetchone()
                == (expected.user,), "upgrade_snapshot_owner_mismatch")
        grants = conn.execute("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
            FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.oid=%s::regclass AND a.grantee<>c.relowner ORDER BY 1,2,3""", (relation,)).fetchall()
        require(grants == [(expected.reader_role, "SELECT", False)], "upgrade_reader_snapshot_permissions_mismatch")


def verify_rows(conn: psycopg.Connection, artifacts: tuple, *, snapshot: bool = False) -> None:
    manifest, courses, units, vectors, _query = artifacts
    dataset = manifest["dataset_id"]
    course_table = "snapshot_course" if snapshot else "course"
    unit_table = "snapshot_unit" if snapshot else "course_unit"
    clause = sql.SQL(" WHERE dataset_id=%s") if snapshot else sql.SQL("")
    params = (dataset,) if snapshot else ()
    rows = conn.execute(sql.SQL("SELECT course_id,tme,metadata{} FROM work24_bge.{}{}").format(
        sql.SQL(",source_dataset_id" if snapshot else ""), sql.Identifier(course_table), clause), params).fetchall()
    expected_courses = {(course["course_id"], course["tme"]): (course, dataset) if snapshot else (course,) for course in courses}
    require(len(rows) == len(courses) and {(row[0], row[1]): row[2:] for row in rows} == expected_courses,
            "upgrade_course_content_mismatch")
    fields = UNIT_FIELDS + (["source_dataset_id"] if snapshot else [])
    rows = conn.execute(sql.SQL("SELECT {} FROM work24_bge.{}{} ORDER BY vector_row").format(
        sql.SQL(",").join(map(sql.Identifier, fields)), sql.Identifier(unit_table), clause), params).fetchall()
    require(len(rows) == len(units), "upgrade_unit_count_mismatch")
    for unit, vector, row in zip(units, vectors, rows, strict=True):
        actual = dict(zip(fields, row, strict=True))
        stored_vector = vector_array(actual.pop("embedding"))
        require(stored_vector.shape == vector.shape and stored_vector.astype("<f4", copy=False).tobytes()
                == vector.astype("<f4", copy=False).tobytes(), "upgrade_vector_bytes_mismatch")
        expected = {field: unit[field] for field in UNIT_FIELDS if field not in {"embedding", "profile_id", "dataset_id", "active"}}
        expected.update(profile_id=manifest["embedding_profile_id"], dataset_id=dataset, active=True)
        if snapshot:
            expected["source_dataset_id"] = dataset
        require(actual == expected, "upgrade_unit_content_mismatch")


def verify_base(conn: psycopg.Connection, artifacts: tuple, expected: Expected, state: str) -> None:
    manifest = artifacts[0]
    require((manifest["embedding_profile_id"], manifest["dataset_id"]) == (expected.profile_id, expected.dataset_id),
            "upgrade_package_identity_mismatch")
    require(conn.execute("SELECT config FROM work24_bge.embedding_profile WHERE profile_id=%s", (expected.profile_id,)).fetchone()
            == (manifest["embedding_config"],), "upgrade_profile_mismatch")
    require(conn.execute("SELECT profile_id,manifest FROM work24_bge.dataset_snapshot WHERE dataset_id=%s", (expected.dataset_id,)).fetchone()
            == (expected.profile_id, manifest), "upgrade_manifest_mismatch")
    if state == "legacy":
        require(conn.execute("SELECT (SELECT count(*) FROM work24_bge.embedding_profile),(SELECT count(*) FROM work24_bge.dataset_snapshot)").fetchone()
                == (1, 1), "upgrade_legacy_catalog_mismatch")
    verify_rows(conn, artifacts)


def verify_upgraded(conn: psycopg.Connection, artifacts: tuple, expected: Expected) -> str:
    verify_rows(conn, artifacts, snapshot=True)
    history = conn.execute("SELECT previous_dataset_id,dataset_id,batch_id FROM work24_bge.catalog_activation ORDER BY id").fetchall()
    require(bool(history) and history[0] == (None, expected.dataset_id, None), "upgrade_activation_history_mismatch")
    previous = expected.dataset_id
    for parent, dataset, batch in history[1:]:
        require(parent == previous and batch is not None, "upgrade_activation_history_mismatch")
        previous = dataset
    require(conn.execute("SELECT singleton,dataset_id FROM work24_bge.active_catalog").fetchall() == [(True, previous)],
            "upgrade_active_pointer_mismatch")
    require(conn.execute("SELECT profile_id FROM work24_bge.dataset_snapshot WHERE dataset_id=%s", (previous,)).fetchone()
            == (expected.profile_id,), "upgrade_active_profile_mismatch")
    require(conn.execute("""SELECT EXISTS(SELECT 1 FROM work24_bge.snapshot_course WHERE dataset_id=%s),
        EXISTS(SELECT 1 FROM work24_bge.snapshot_unit WHERE dataset_id=%s AND active)""", (previous, previous)).fetchone()
            == (True, True), "upgrade_active_snapshot_missing")
    verify_snapshot_permissions(conn, expected)
    return previous


def mutation_plan(conn: psycopg.Connection, artifacts: tuple, expected: Expected, state: str) -> dict:
    operations = []
    if state == "legacy":
        operations = [
            {"action": "create_tables_with_constraints", "tables": list(SNAPSHOT_TABLES), "ddl_sha256": SNAPSHOT_DDL_SHA256},
            {"action": "create_indexes", "indexes": [*(name + "_pkey" for name in SNAPSHOT_TABLES), SNAPSHOT_INDEX]},
            {"action": "create_identity_sequences", "sequences": ["snapshot_unit_id_seq", "catalog_activation_id_seq"]},
            {"action": "copy_baseline_courses", "source": "course", "destination": "snapshot_course", "rows": len(artifacts[1])},
            {"action": "copy_baseline_units", "source": "course_unit", "destination": "snapshot_unit", "rows": len(artifacts[2])},
            {"action": "insert_initial_pointer", "table": "active_catalog", "rows": 1, "dataset_id": expected.dataset_id},
            {"action": "insert_initial_history", "table": "catalog_activation", "rows": 1, "dataset_id": expected.dataset_id},
            {"action": "grant_select", "tables": list(SNAPSHOT_TABLES), "role": expected.reader_role},
        ]
    identity = conn.execute("SELECT current_database(),current_user,inet_server_addr()::text,inet_server_port(),(SELECT oid FROM pg_database WHERE datname=current_database())").fetchone()
    host = conn.info.host
    endpoint = {"host": str(Path(host).resolve()) if host.startswith("/") else host, "port": conn.info.port}
    return {"namespace": "work24_bge", "target": list(identity), "endpoint": endpoint, "source_state": state,
            "profile_id": expected.profile_id, "dataset_id": expected.dataset_id,
            "manifest_sha256": value_hash(artifacts[0]), "archive_sha256": ARCHIVE_SHA256,
            "reader_role": expected.reader_role, "operations": operations}


def upgrade(conn: psycopg.Connection, artifacts: tuple, expected: Expected, *, apply: bool = False,
            expected_plan_sha256: str | None = None) -> dict:
    """Caller owns the transaction; any exception must roll back the entire operation."""
    # Read after acquiring table locks so writes committed while waiting cannot
    # leave verification and copying on an older MVCC snapshot.
    conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED" if apply
                 else "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.execute("SET LOCAL lock_timeout='10s'")
    conn.execute("SET LOCAL statement_timeout='120s'")
    conn.execute("SET LOCAL search_path=pg_catalog,public")
    require(file_hash(Path(__file__).with_name("snapshot_schema.sql")) == SNAPSHOT_DDL_SHA256,
            "upgrade_ddl_checksum_mismatch")
    if apply:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('work24_bge_snapshot_load'))")
    attest(conn, expected)
    if apply:
        conn.execute(sql.SQL("LOCK TABLE {} IN SHARE MODE").format(sql.SQL(",").join(
            sql.Identifier("work24_bge", name) for name in BASE_TABLES)))
    register_vector(conn)
    state = snapshot_state(conn)
    if apply and state == "upgraded":
        conn.execute(sql.SQL("LOCK TABLE {} IN SHARE MODE").format(sql.SQL(",").join(
            sql.Identifier("work24_bge", name) for name in SNAPSHOT_TABLES)))
    verify_base(conn, artifacts, expected, state)
    plan = mutation_plan(conn, artifacts, expected, state)
    plan_sha256 = value_hash(plan)
    active = expected.dataset_id
    if state == "upgraded":
        active = verify_upgraded(conn, artifacts, expected)
    elif apply:
        require(expected_plan_sha256 == plan_sha256, "upgrade_plan_mismatch")
        conn.execute(Path(__file__).with_name("snapshot_schema.sql").read_text(), prepare=False)
        # Validate inherited defaults/identity before any insert can evaluate a
        # copied legacy nextval() default (sequence advances do not roll back).
        require(snapshot_state(conn) == "upgraded", "catalog_upgrade_schema_mismatch")
        conn.execute("""INSERT INTO work24_bge.snapshot_course
            SELECT %s,course_id,tme,metadata,%s FROM work24_bge.course""", (expected.dataset_id, expected.dataset_id))
        fields = sql.SQL(",").join(map(sql.Identifier, UNIT_FIELDS))
        conn.execute(sql.SQL("""INSERT INTO work24_bge.snapshot_unit ({},source_dataset_id)
            SELECT {},dataset_id FROM work24_bge.course_unit""").format(fields, fields))
        conn.execute("INSERT INTO work24_bge.active_catalog(dataset_id) VALUES(%s)", (expected.dataset_id,))
        conn.execute("INSERT INTO work24_bge.catalog_activation(dataset_id) VALUES(%s)", (expected.dataset_id,))
        for name in SNAPSHOT_TABLES:
            conn.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                sql.Identifier("work24_bge", name), sql.Identifier(expected.reader_role)))
        verify_upgraded(conn, artifacts, expected)
        verify_rows(conn, artifacts)
    views = conn.execute("""SELECT c.relname,pg_get_viewdef(c.oid,true) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='work24_bge' AND c.relkind IN ('v','m') ORDER BY c.relname""").fetchall()
    return {"status": "already_upgraded" if state == "upgraded" else "applied" if apply else "planned",
            "profile_id": expected.profile_id, "dataset_id": expected.dataset_id, "active_dataset_id": active,
            "courses": len(artifacts[1]), "units": len(artifacts[2]), "archive_sha256": ARCHIVE_SHA256,
            "reader_role": expected.reader_role, "existing_views": [row[0] for row in views],
            "base_content_verified": True, "plan": plan, "plan_sha256": plan_sha256,
            "applied": apply and state == "legacy"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="Existing pinned package; never downloaded")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-user", required=True)
    parser.add_argument("--expected-profile-id", required=True)
    parser.add_argument("--expected-dataset-id", required=True)
    parser.add_argument("--reader-role", required=True)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed additive upgrade")
    parser.add_argument("--expected-plan-sha256", help="Required for a mutating apply; use the reviewed read-only plan hash")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.report:
            require(args.report.resolve() != args.archive.resolve(), "upgrade_report_archive_collision")
            require(not args.report.exists(), "upgrade_report_already_exists")
        expected = Expected(args.expected_database, args.expected_user, args.expected_profile_id,
                            args.expected_dataset_id, args.reader_role)
        params = connection_parameters(dict(os.environ), expected)
        with TemporaryDirectory(prefix="catalog-upgrade-") as directory:
            extract_verified(args.archive, Path(directory) / "data")
            artifacts = load_artifacts(Path(directory) / "data")
            with psycopg.connect(**params) as conn:
                report = upgrade(conn, artifacts, expected, apply=args.apply,
                                 expected_plan_sha256=args.expected_plan_sha256)
        if args.report:
            temporary = None
            try:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.report.parent,
                                        prefix=".catalog-upgrade-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(json.dumps(report, indent=2) + "\n")
                # Atomic exclusive publication: never replace an existing file,
                # including one created after the preflight path check.
                os.link(temporary, args.report)
            except OSError:
                # The database context has already committed. Never present an
                # output-file failure as a database rollback or request a retry.
                print(json.dumps({**report, "database_status": report["status"],
                                  "status": "committed_report_write_failed" if args.apply else "report_write_failed"}))
                return 2
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass  # Cleanup cannot change the committed database outcome.
        print(json.dumps(report))
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z0-9_]+", str(exc)) else "catalog_upgrade_failed"
        print(f"Catalog upgrade failed ({code}). Connection details omitted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
