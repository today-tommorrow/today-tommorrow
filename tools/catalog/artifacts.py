"""Read the pinned education package without importing or executing its code."""

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile

ARCHIVE_NAME = "work24-bge-m3-20261101.zip"
ARCHIVE_SHA256 = "c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e"
SOURCE_COMMIT = "043f8b8e96620994aff06b0edc2d235f793f0b5a"
ARCHIVE_URL = f"https://raw.githubusercontent.com/seven2762/tdtemp/{SOURCE_COMMIT}/{ARCHIVE_NAME}"
DATA_FILES = {
    "manifest.json", "embedding-config.json", "courses.jsonl", "units.jsonl",
    "vectors.npy", "smoke-query.npy", "truncation-report.json",
    "local-search-results.json", "validation-report.json",
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def value_hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_path(repo, explicit=None):
    if explicit is not None:
        return Path(explicit).resolve()
    local = repo / "tdtemp" / ARCHIVE_NAME
    if local.is_file():
        return local
    cache = repo / ".local" / "catalog"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / ARCHIVE_NAME
    if destination.is_file() and file_hash(destination) == ARCHIVE_SHA256:
        return destination
    temporary = None
    try:
        with NamedTemporaryFile(dir=cache, prefix="download-", delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(ARCHIVE_URL, timeout=30) as response:
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    require(size <= 40 * 1024 * 1024, "archive_size_limit")
                    output.write(block)
        require(file_hash(temporary) == ARCHIVE_SHA256, "archive_checksum_mismatch")
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_verified(archive, destination):
    require(file_hash(archive) == ARCHIVE_SHA256, "archive_checksum_mismatch")
    prefix = "work24-bge-m3-20261101/"
    with zipfile.ZipFile(archive) as package:
        entries = [package.getinfo(prefix + name) for name in sorted(DATA_FILES)]
        require(sum(entry.file_size for entry in entries) <= 100 * 1024 * 1024, "artifact_size_limit")
        destination.mkdir(parents=True, exist_ok=False)
        # Fixed basenames only: no extractall, package Python, or archive-supplied paths.
        for entry in entries:
            with package.open(entry) as source, (destination / Path(entry.filename).name).open("xb") as output:
                shutil.copyfileobj(source, output)


def load_artifacts(root):
    import numpy as np

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(value_hash(manifest["embedding_config"]) == manifest["embedding_profile_id"], "embedding_profile_mismatch")
    require(value_hash(manifest["input_files"]) == manifest["dataset_id"], "dataset_identity_mismatch")
    require(set(manifest["artifacts"]) == DATA_FILES - {"manifest.json"}, "artifact_manifest_mismatch")
    for name, info in manifest["artifacts"].items():
        path = root / name
        require(path.stat().st_size == info["bytes"] and file_hash(path) == info["sha256"], "artifact_checksum_mismatch")
    require(json.loads((root / "embedding-config.json").read_text()) == manifest["embedding_config"], "embedding_config_mismatch")
    courses = [json.loads(line) for line in (root / "courses.jsonl").read_text().splitlines() if line]
    units = [json.loads(line) for line in (root / "units.jsonl").read_text().splitlines() if line]
    vectors = np.load(root / "vectors.npy", allow_pickle=False)
    query = np.load(root / "smoke-query.npy", allow_pickle=False)
    require(len(courses) == 406 and len(units) == 6099, "artifact_row_count_mismatch")
    require(vectors.shape == (6099, 1024) and vectors.dtype == np.float32, "vector_shape_mismatch")
    require(query.shape == (1024,) and query.dtype == np.float32, "query_shape_mismatch")
    keys = {(course["course_id"], course["tme"]) for course in courses}
    require(len(keys) == 406 and len({unit["unit_key"] for unit in units}) == 6099, "duplicate_artifact_key")
    for row, unit in enumerate(units):
        require(unit["vector_row"] == row, "vector_row_mismatch")
        require((unit["course_id"], unit["tme"]) in keys, "course_unit_orphan")
        require(hashlib.sha256(unit["embed_text"].encode()).hexdigest() == unit["text_sha256"], "unit_text_mismatch")
        require(value_hash([unit[key] for key in ("course_id", "tme", "unit_type", "ncs_unit_code", "unit_name", "embed_text")]) == unit["unit_key"], "unit_key_mismatch")
        require(unit["embedding_profile_id"] == manifest["embedding_profile_id"] and unit["dataset_id"] == manifest["dataset_id"], "unit_profile_mismatch")
        require(unit["is_noise"] is False and isinstance(unit["is_inferred"], bool), "unit_origin_mismatch")
    norms = np.linalg.norm(vectors.astype(np.float64), axis=1)
    require(np.isfinite(vectors).all() and np.max(np.abs(norms - 1)) < 1e-5, "vector_norm_mismatch")
    require(np.isfinite(query).all() and abs(float(np.linalg.norm(query.astype(np.float64))) - 1) < 1e-5, "query_norm_mismatch")
    return manifest, courses, units, vectors, query
