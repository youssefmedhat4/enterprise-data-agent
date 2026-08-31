#!/usr/bin/env bash
set -Eeuo pipefail

# Schema first, then the deterministic fixture data. ON_ERROR_STOP so a broken
# fixture fails the container rather than leaving a half-seeded database that
# would silently change reference results.
psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  < /opt/erp-init/001_legacy_erp.sql

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --command "SET erp.readonly_password = '${ERP_READONLY_PASSWORD}'" \
  --file /opt/erp-init/002_legacy_seed.sql
