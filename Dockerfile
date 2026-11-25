FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir -e .

FROM base AS test
RUN pip install --no-cache-dir -e ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
RUN useradd --create-home app && mkdir -p /srv/data && chown app /srv/data
COPY docker/entrypoint.sh /entrypoint.sh
ENV DATA_DIR=/srv/data
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
