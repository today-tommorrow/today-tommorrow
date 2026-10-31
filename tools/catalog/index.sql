CREATE INDEX IF NOT EXISTS course_unit_embedding_hnsw
ON work24_bge.course_unit USING hnsw (embedding vector_cosine_ops)
WHERE active;
