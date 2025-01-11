#!/bin/sh
# Seed the database before serving, so the API is never empty on first boot.
# `app.seed` is idempotent: it returns immediately if rows already exist, so
# restarting the container does not duplicate the dataset.
set -e

if [ "${SEED_ON_START:-true}" = "true" ]; then
    echo "[entrypoint] seeding database (skipped if already populated)..."
    python -m app.seed
fi

echo "[entrypoint] starting: $*"
exec "$@"
