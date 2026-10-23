# Hippocampus API image (HIPPO-E04-F1). Build from the repo root:  podman build -f deploy/api.Dockerfile -t localhost/hippocampus-api:latest .
FROM docker.io/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HIPPO_MODEL_CACHE=/opt/model-cache PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[db,embed,api,browser]"
# Chromium for the crawler's "browser" engine (JavaScript-only sites); system libs need root, so this runs before USER hippo
# INSTALL_BROWSER=0 (the local compose default) skips the ~400 MB Chromium; the "browser" crawl engine then reports that it is unavailable.
ARG INSTALL_BROWSER=1
RUN if [ "$INSTALL_BROWSER" = "1" ]; then playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /opt/pw-browsers; fi
# Bake the embedding model in so the container needs no internet at runtime
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/opt/model-cache', providers=['CPUExecutionProvider'])"
# The migrations travel with the image so `python -m hippocampus.migrate` can run before the API starts (compose.yaml)
COPY db ./db
ENV HIPPO_MIGRATIONS_DIR=/app/db/migrations
RUN useradd --system --uid 10001 hippo && chown -R hippo /opt/model-cache
USER hippo
EXPOSE 8090
CMD ["uvicorn", "--factory", "hippocampus.api:app_from_env", "--host", "0.0.0.0", "--port", "8090"]
