#!/usr/bin/env bash
set -e

# ждём Postgres
if [ -n "${DB_HOST}" ]; then
  echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT:-5432} ..."
  for i in {1..60}; do
    if curl -s "http://${DB_HOST}:${DB_PORT:-5432}" >/dev/null 2>&1; then
      break
    fi
    # pg порт не HTTP — используем pg_isready, если доступен
    if command -v pg_isready >/dev/null 2>&1; then
      pg_isready -h "${DB_HOST}" -p "${DB_PORT:-5432}" -U "${DB_USER}" && break
    fi
    sleep 1
  done
fi

# миграции + collectstatic (без БД не требуется, но и не мешает)
python manage.py migrate --noinput
python manage.py collectstatic --noinput || true

# тестовые данные (один раз, по флагу)
if [ "${SEED_DATA}" = "1" ]; then
  echo "Seeding demo data..."
  python manage.py seed_repairs || true
fi

exec "$@"
