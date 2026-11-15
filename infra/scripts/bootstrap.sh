#!/usr/bin/env sh

set -eu

ROOT_DIR=$(CDPATH="" cd -- "$(dirname -- "$0")/../.." && pwd)
ENV_FILE="$ROOT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo ".env not found at $ENV_FILE"
  echo "Copy .env.example to .env before starting local services."
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

API_PORT=${API_PORT:-8000}


echo "Environment file found: $ENV_FILE"
echo "API: http://localhost:$API_PORT/health"
echo "Web UI (served by the API): http://localhost:$API_PORT"
