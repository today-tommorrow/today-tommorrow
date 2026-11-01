-- Immutable effective snapshots. The original pinned bootstrap tables stay intact.
CREATE TABLE IF NOT EXISTS work24_bge.snapshot_course (
  dataset_id text NOT NULL REFERENCES work24_bge.dataset_snapshot(dataset_id),
  course_id text NOT NULL,
  tme text NOT NULL,
  metadata jsonb NOT NULL,
  source_dataset_id text NOT NULL,
  PRIMARY KEY(dataset_id,course_id,tme)
);
CREATE TABLE IF NOT EXISTS work24_bge.snapshot_unit (
  LIKE work24_bge.course_unit INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING IDENTITY,
  source_dataset_id text NOT NULL,
  PRIMARY KEY(dataset_id,unit_key),
  FOREIGN KEY(dataset_id,course_id,tme)
    REFERENCES work24_bge.snapshot_course(dataset_id,course_id,tme)
);
CREATE INDEX IF NOT EXISTS snapshot_unit_embedding_hnsw
  ON work24_bge.snapshot_unit USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS work24_bge.catalog_batch (
  batch_id text PRIMARY KEY CHECK(length(batch_id)=64),
  parent_dataset_id text NOT NULL REFERENCES work24_bge.dataset_snapshot(dataset_id),
  dataset_id text REFERENCES work24_bge.dataset_snapshot(dataset_id),
  manifest jsonb NOT NULL,
  state text NOT NULL CHECK(state IN ('draft','ready','failed','activated')),
  report jsonb NOT NULL DEFAULT '{}',
  failure_history jsonb NOT NULL DEFAULT '[]',
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS work24_bge.active_catalog (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  dataset_id text NOT NULL REFERENCES work24_bge.dataset_snapshot(dataset_id)
);
CREATE TABLE IF NOT EXISTS work24_bge.catalog_activation (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  previous_dataset_id text REFERENCES work24_bge.dataset_snapshot(dataset_id),
  dataset_id text NOT NULL REFERENCES work24_bge.dataset_snapshot(dataset_id),
  batch_id text REFERENCES work24_bge.catalog_batch(batch_id),
  activated_at timestamptz NOT NULL DEFAULT now()
);
