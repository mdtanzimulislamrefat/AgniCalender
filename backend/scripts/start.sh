#!/bin/sh
# Container entry point: migrate the schema, then serve with the NASA fetch scheduler.
set -e
python -m backend.storage.manage migrate
# The trained classifier ships in backend/models; training here would exceed small hosts' memory.
exec python -m backend.api.server --host 0.0.0.0 --port "${PORT:-8000}" \
    --fetch-every "${FETCH_EVERY:-3h}" --behind-proxy
