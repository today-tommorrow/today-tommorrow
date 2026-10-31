"""Local development-only role provisioning and model-free vector verification."""

from pathlib import Path

import numpy as np
import psycopg
from artifacts import require
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

UNIT_FIELDS = [
    "unit_key", "course_id", "tme", "vector_row", "unit_type", "ncs_unit_code",
    "unit_name", "embed_text", "text_sha256", "hours", "source", "is_noise",
    "is_inferred", "provenance", "embedding", "profile_id", "dataset_id", "active",
]
ROLES = {"catalog": "today_bootstrap", "migration": "today_migrator", "app": "today_app"}


def local_connections(env):
    require(not any(env.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")), "invalid_local_database_target")
    connections = {}
    try:
        for kind, key in (("catalog", "CATALOG_DATABASE_URL"), ("migration", "MIGRATION_DATABASE_URL"), ("app", "DATABASE_URL")):
            dsn = env[key].replace("postgresql+psycopg://", "postgresql://", 1)
            params = conninfo_to_dict(dsn)
            require(set(params) <= {"host", "port", "dbname", "user", "password", "connect_timeout", "sslmode"}, "invalid_local_database_target")
            require(params.get("host") == "127.0.0.1" and params.get("dbname") == "work24_dev", "invalid_local_database_target")
            require(params.get("user") == ROLES[kind] and bool(params.get("password")), "invalid_local_database_target")
            port = int(params.get("port") or "5432")
            require(1 <= port <= 65535, "invalid_local_database_target")
            params.update(port=str(port), connect_timeout="15", hostaddr="127.0.0.1", options="")
            connections[kind] = params
        require(len({params["port"] for params in connections.values()}) == 1, "invalid_local_database_target")
    except (KeyError, TypeError, ValueError, psycopg.Error):
        raise ValueError("invalid_local_database_target") from None
    return connections


def attest(conn, role):
    database, user, version = conn.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int").fetchone()
    require(database == "work24_dev" and user == role, "unexpected_database_identity")
    require(160000 <= version < 170000, "postgresql_16_required")


def provision(conn, connections):
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    require(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0] == "0.6.0", "pgvector_060_required")
    for kind in ("migration", "app"):
        role = ROLES[kind]
        existing = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        if existing is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role), sql.Literal(connections[kind]["password"])))
        else:
            require(not any(existing[:5]) and existing[5], "existing_role_privileges_mismatch")
        membership = conn.execute("SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)", (role,)).fetchone()
        require(membership is None, "existing_role_membership_mismatch")
    conn.execute("CREATE SCHEMA IF NOT EXISTS today_app AUTHORIZATION today_migrator")
    owner = conn.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='today_app'").fetchone()[0]
    require(owner == "today_migrator", "application_schema_owner_mismatch")
    conn.execute("REVOKE ALL ON DATABASE work24_dev FROM PUBLIC")
    conn.execute("GRANT CONNECT ON DATABASE work24_dev TO today_app, today_migrator")
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.execute("REVOKE CREATE ON SCHEMA today_app FROM PUBLIC, today_app")
    conn.execute("GRANT USAGE ON SCHEMA today_app TO today_app")
    conn.execute("ALTER DEFAULT PRIVILEGES FOR ROLE today_migrator IN SCHEMA today_app GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO today_app")
    conn.execute("ALTER DEFAULT PRIVILEGES FOR ROLE today_migrator IN SCHEMA today_app GRANT USAGE, SELECT ON SEQUENCES TO today_app")


def grant_catalog_read(conn):
    owner = conn.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='work24_bge'").fetchone()[0]
    require(owner == "today_bootstrap", "education_schema_owner_mismatch")
    conn.execute("REVOKE ALL ON SCHEMA work24_bge FROM PUBLIC, today_app, today_migrator")
    conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA work24_bge FROM PUBLIC, today_app, today_migrator")
    conn.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA work24_bge FROM PUBLIC, today_app, today_migrator")
    conn.execute("GRANT USAGE ON SCHEMA work24_bge TO today_app")
    conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA work24_bge TO today_app")
    conn.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA work24_bge GRANT SELECT ON TABLES TO today_app")


def vector_array(stored):
    return np.asarray(stored.to_numpy() if hasattr(stored, "to_numpy") else stored, dtype=np.float32)


def load_catalog(conn, artifacts):
    manifest, courses, units, vectors, _query = artifacts
    profile, dataset = manifest["embedding_profile_id"], manifest["dataset_id"]
    conn.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)
    previous = conn.execute("SELECT DISTINCT profile_id, dataset_id FROM work24_bge.course_unit").fetchall()
    require(all(row == (profile, dataset) for row in previous), "different_catalog_snapshot_present")
    conn.execute("CREATE TEMP TABLE stage_course (course_id text, tme text, metadata jsonb, PRIMARY KEY(course_id,tme)) ON COMMIT DROP")
    conn.execute("""CREATE TEMP TABLE stage_unit (
        unit_key text PRIMARY KEY, course_id text, tme text, vector_row integer,
        unit_type text, ncs_unit_code text, unit_name text, embed_text text, text_sha256 text,
        hours double precision, source text, is_noise boolean, is_inferred boolean,
        provenance jsonb, embedding vector(1024), profile_id text, dataset_id text, active boolean
    ) ON COMMIT DROP""")
    with conn.cursor().copy("COPY stage_course (course_id,tme,metadata) FROM STDIN") as copy:
        for course in courses:
            copy.write_row((course["course_id"], course["tme"], Jsonb(course)))
    fields = ",".join(UNIT_FIELDS)
    with conn.cursor().copy(f"COPY stage_unit ({fields}) FROM STDIN") as copy:
        for unit, vector in zip(units, vectors, strict=True):
            row = {**unit, "embedding": np.asarray(vector), "profile_id": profile, "dataset_id": dataset,
                   "active": True, "provenance": Jsonb(unit["provenance"])}
            copy.write_row(tuple(row[field] for field in UNIT_FIELDS))
    require(conn.execute("SELECT count(*) FROM stage_course").fetchone()[0] == 406, "staged_course_count_mismatch")
    require(conn.execute("SELECT count(*) FROM stage_unit").fetchone()[0] == 6099, "staged_unit_count_mismatch")
    require(conn.execute("SELECT count(*) FROM stage_unit u LEFT JOIN stage_course c USING(course_id,tme) WHERE c.course_id IS NULL").fetchone()[0] == 0, "staged_orphan_unit")
    conn.execute("INSERT INTO work24_bge.embedding_profile(profile_id,config) VALUES(%s,%s) ON CONFLICT DO NOTHING", (profile, Jsonb(manifest["embedding_config"])))
    stored_config = conn.execute("SELECT config FROM work24_bge.embedding_profile WHERE profile_id=%s", (profile,)).fetchone()[0]
    require(stored_config == manifest["embedding_config"], "stored_profile_mismatch")
    conn.execute("""INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(%s,%s,%s)
        ON CONFLICT(dataset_id) DO UPDATE SET manifest=excluded.manifest""", (dataset, profile, Jsonb(manifest)))
    conn.execute("""INSERT INTO work24_bge.course SELECT * FROM stage_course
        ON CONFLICT(course_id,tme) DO UPDATE SET metadata=excluded.metadata""")
    updates = ",".join(f"{field}=excluded.{field}" for field in UNIT_FIELDS if field != "unit_key")
    upsert = f"INSERT INTO work24_bge.course_unit ({fields}) SELECT {fields} FROM stage_unit ON CONFLICT(unit_key) DO UPDATE SET {updates}"
    conn.execute(upsert)
    first = conn.execute("SELECT count(*) FROM work24_bge.course_unit").fetchone()[0]
    conn.execute(upsert)
    require(first == conn.execute("SELECT count(*) FROM work24_bge.course_unit").fetchone()[0] == 6099, "repeat_upsert_count_mismatch")


def ranking_comparison(local_scores, remote_rows, top_k=10, tolerance=1e-5):
    remote = {key: float(score) for key, score in remote_rows}
    require(set(remote) == set(local_scores), "search_unit_set_mismatch")
    max_error = max(abs(local_scores[key] - remote[key]) for key in local_scores)
    local_order = sorted(local_scores, key=lambda key: (-local_scores[key], key))
    remote_order = sorted(remote, key=lambda key: (-remote[key], key))
    cutoff = local_scores[local_order[top_k - 1]]
    mandatory = {key for key, score in local_scores.items() if score > cutoff + tolerance}
    remote_top = set(remote_order[:top_k])
    tie_match = mandatory <= remote_top and all(local_scores[key] >= cutoff - tolerance for key in remote_top)
    return {"max_absolute_similarity_error": max_error, "tolerance": tolerance,
            "tie_aware_top10_match": tie_match, "passed": max_error <= tolerance and tie_match}


def verify_catalog(conn, artifacts, create_index=False):
    manifest, courses, units, vectors, query = artifacts
    profile, dataset = manifest["embedding_profile_id"], manifest["dataset_id"]
    counts = conn.execute("SELECT count(*), count(DISTINCT(course_id,tme)) FROM work24_bge.course_unit WHERE profile_id=%s AND dataset_id=%s AND active", (profile, dataset)).fetchone()
    require(counts == (6099, 406), "catalog_count_mismatch")
    stored_courses = conn.execute("SELECT course_id,tme,metadata FROM work24_bge.course").fetchall()
    require({(key, term): metadata for key, term, metadata in stored_courses} == {(course["course_id"], course["tme"]): course for course in courses}, "stored_course_metadata_mismatch")
    rows = conn.execute(f"""SELECT {','.join(UNIT_FIELDS)}
        FROM work24_bge.course_unit WHERE profile_id=%s AND dataset_id=%s AND active ORDER BY vector_row""", (profile, dataset)).fetchall()
    require(len(rows) == 6099, "roundtrip_count_mismatch")
    for expected_row, values in enumerate(rows):
        unit = units[expected_row]
        stored = dict(zip(UNIT_FIELDS, values, strict=True))
        require(stored["unit_key"] == unit["unit_key"] and stored["vector_row"] == expected_row, "roundtrip_key_mismatch")
        require(np.array_equal(vector_array(stored.pop("embedding")), vectors[expected_row]), "roundtrip_vector_mismatch")
        expected = {key: unit[key] for key in UNIT_FIELDS if key not in {"embedding", "profile_id", "dataset_id", "active"}}
        expected.update(profile_id=profile, dataset_id=dataset, active=True)
        require(stored == expected, "roundtrip_metadata_mismatch")
    conn.execute("SET LOCAL enable_indexscan=off")
    conn.execute("SET LOCAL enable_indexonlyscan=off")
    conn.execute("SET LOCAL enable_bitmapscan=off")
    vector64, query64 = vectors.astype(np.float64), query.astype(np.float64)
    scores = vector64 @ query64 / (np.linalg.norm(vector64, axis=1) * np.linalg.norm(query64))
    local = {unit["unit_key"]: float(score) for unit, score in zip(units, scores, strict=True)}
    params = (query, profile, dataset)
    exact = conn.execute("SELECT unit_key,1-(embedding <=> %s) FROM work24_bge.course_unit WHERE profile_id=%s AND dataset_id=%s AND active", params).fetchall()
    comparison = ranking_comparison(local, exact)
    require(comparison["passed"], "exact_search_mismatch")
    if create_index:
        conn.execute(Path(__file__).with_name("index.sql").read_text(), prepare=False)
        # Fresh imports have no column statistics. Without them, the planner
        # estimates the two snapshot predicates as one row and sorts a B-tree scan.
        conn.execute("ANALYZE work24_bge.course_unit")
    index = conn.execute("""SELECT am.amname, idx.indisvalid FROM pg_index idx
        JOIN pg_class relation ON relation.oid=idx.indexrelid
        JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
        JOIN pg_am am ON am.oid=relation.relam
        WHERE namespace.nspname='work24_bge' AND relation.relname='course_unit_embedding_hnsw'""").fetchone()
    require(index == ("hnsw", True), "hnsw_index_missing_or_invalid")
    conn.execute("SET LOCAL enable_indexscan=on")
    conn.execute("SET LOCAL enable_indexonlyscan=on")
    conn.execute("SET LOCAL enable_bitmapscan=on")
    # Force an available ANN index only for this diagnostic; normal query planning is unchanged.
    conn.execute("SET LOCAL enable_seqscan=off")
    conn.execute("SET LOCAL hnsw.ef_search=100")
    ann_sql = """SELECT unit_key,1-(embedding <=> %s) AS similarity FROM work24_bge.course_unit
        WHERE profile_id=%s AND dataset_id=%s AND active ORDER BY embedding <=> %s LIMIT 10"""
    ann_params = params + (query,)
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + ann_sql, ann_params).fetchone()[0]
    used = "course_unit_embedding_hnsw" in __import__("json").dumps(plan)
    ann = conn.execute(ann_sql, ann_params).fetchall()
    local_top = set(sorted(local, key=lambda key: (-local[key], key))[:10])
    return {"courses": 406, "units": 6099, "full_float32_roundtrip_rows": len(rows),
            "dataset_id": dataset, "profile_id": profile,
            "exact_search": comparison,
            "hnsw": {"index_used": used, "top10_overlap_vs_exact": len(local_top & {row[0] for row in ann}) / 10,
                     "note": "Stored-query diagnostic only; no new-input/model quality claim."},
            "postgres_version": conn.info.server_version,
            "pgvector_version": conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]}


def verify_roles(connections):
    for kind in ("migration", "app"):
        with psycopg.connect(**connections[kind]) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            attest(conn, ROLES[kind])
            permissions = conn.execute("""SELECT
                has_schema_privilege(current_user,'today_app','CREATE'),
                has_table_privilege(current_user, (SELECT relation.oid FROM pg_class relation
                    JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                    WHERE namespace.nspname='work24_bge' AND relation.relname='course'), 'SELECT'),
                has_database_privilege(current_user,current_database(),'CREATE')""").fetchone()
            if permissions is None:
                raise ValueError("database_role_boundary_mismatch")
            create_app, read_catalog, create_database = permissions
            require(create_app == (kind == "migration") and read_catalog == (kind == "app")
                    and not create_database, "database_role_boundary_mismatch")
            if kind == "app":
                require(conn.execute("SELECT count(*) FROM work24_bge.course").fetchone() == (406,), "application_catalog_read_mismatch")
                write_catalog = conn.execute("SELECT has_table_privilege(current_user,'work24_bge.course','INSERT,UPDATE,DELETE,TRUNCATE')").fetchone()
                require(write_catalog is not None and not write_catalog[0], "application_catalog_write_forbidden")
    return True


def execute(connections, artifacts, verify_only=False):
    with psycopg.connect(**connections["catalog"]) as conn:
        if verify_only:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        attest(conn, ROLES["catalog"])
        if verify_only:
            register_vector(conn)
        else:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('work24_bge_snapshot_load'))")
            provision(conn, connections)
            register_vector(conn)
            load_catalog(conn, artifacts)
            grant_catalog_read(conn)
        report = verify_catalog(conn, artifacts, create_index=not verify_only)
    # New roles are visible to fresh authentication only after the transaction commits.
    # Existing role passwords are never reset, so check the supplied credentials too.
    report["roles_verified"] = verify_roles(connections)
    # Only report success after the commit and actual application/migration connections.
    report["status"] = "verified" if verify_only else "committed"
    return report
