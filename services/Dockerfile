# Shared image for the Python services. Build with --build-arg PACKAGE=agenttrace-ingest (or -worker).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ARG PACKAGE
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY sdk/python sdk/python
COPY services services
COPY examples/demo-agent/pyproject.toml examples/demo-agent/pyproject.toml
RUN mkdir -p examples/demo-agent/src/demo_agent && touch examples/demo-agent/src/demo_agent/__init__.py \
    && uv sync --frozen --no-dev --package ${PACKAGE}
ENV PATH="/app/.venv/bin:$PATH"
