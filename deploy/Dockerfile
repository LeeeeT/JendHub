FROM rust:1.98.1-slim-trixie AS indexer
WORKDIR /build
COPY bend2-lsp-rs bend2-lsp-rs
COPY indexer indexer
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/indexer/target \
    cargo build --locked --release --manifest-path indexer/Cargo.toml \
    && cp indexer/target/release/jendhub-index /usr/local/bin/jendhub-index

FROM python:3.14.8-slim-trixie AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --compile-bytecode

FROM python:3.14.8-slim-trixie
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=indexer /usr/local/bin/jendhub-index /usr/local/bin/jendhub-index
COPY src/jendhub /opt/jendhub/jendhub
RUN python -m compileall -q /opt/jendhub \
    && mkdir -p /data/library \
    && chown -R 65534:65534 /data
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/jendhub \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 65534:65534
WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["python", "-m", "jendhub.services"]
CMD ["site", "--host", "0.0.0.0", "--port", "8000", "--metrics-port", "9000"]
