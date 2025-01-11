# syntax=docker/dockerfile:1

# ---------- build stage ----------------------------------------------------
# Dependencies are compiled into a virtualenv here so the runtime image never
# carries pip, build tooling or compilers.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


# ---------- runtime stage --------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATABASE_URL="sqlite+aiosqlite:////data/carenotes.db"

# curl is needed by the container healthcheck below.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/app

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# SQLite lives on a volume so restarting the container keeps the data.
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
