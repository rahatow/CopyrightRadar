FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir .

FROM base AS test
RUN pip install --no-cache-dir ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
# TODO v0.1: entrypoint must fix ownership of the ./data bind mount (Docker creates it as root).
RUN useradd --create-home app && mkdir -p /srv/data && chown app /srv/data
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
