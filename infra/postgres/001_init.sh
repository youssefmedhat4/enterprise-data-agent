#!/usr/bin/env bash
set -Eeuo pipefail

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=eda_readonly_password="$EDA_READONLY_PASSWORD" \
  < /opt/eda-init/001_init.sql
