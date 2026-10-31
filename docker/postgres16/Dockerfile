FROM postgres:16.14-bookworm@sha256:64154d0babcb1741988719e703419af0382b19953706149f9872fbd0f438efa8 AS vector_build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl postgresql-server-dev-16 \
    && curl -fL --retry 3 https://github.com/pgvector/pgvector/archive/refs/tags/v0.6.0.tar.gz -o /tmp/pgvector.tar.gz \
    && echo "b0cf4ba1ab016335ac8fb1cada0d2106235889a194fffeece217c5bda90b2f19  /tmp/pgvector.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/pgvector.tar.gz -C /tmp \
    && make -C /tmp/pgvector-0.6.0 OPTFLAGS="" \
    && make -C /tmp/pgvector-0.6.0 install

FROM postgres:16.14-bookworm@sha256:64154d0babcb1741988719e703419af0382b19953706149f9872fbd0f438efa8

COPY --from=vector_build /usr/lib/postgresql/16/lib/vector.so /usr/lib/postgresql/16/lib/
COPY --from=vector_build /usr/share/postgresql/16/extension/vector* /usr/share/postgresql/16/extension/
