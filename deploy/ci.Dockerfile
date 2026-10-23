# Test image for CI (.github/workflows/ci.yml). Build from the repo root:  docker build -f deploy/ci.Dockerfile -t hippo-ci .
# It runs scripts/check.sh --ci against the pgvector database named in HIPPO_TEST_DSN. No model, no browser: the tests use fakes.
FROM docker.io/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e ".[db,api,mcp,dev]"
COPY db ./db
COPY scripts ./scripts
COPY tests ./tests
# node is not in this image: CI checks the dashboard JavaScript in its own step, so skip it here (see ci.yml)
ENTRYPOINT ["scripts/check.sh", "--ci", "--skip-js"]
