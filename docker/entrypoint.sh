#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432} ..."
# База у тебя уже имеет healthcheck, зависимость выставлена, но подождём ещё чуть-чуть.
for i in {1..30}; do
  python - <<'PY' >/dev/null 2>&1 && break || sleep 1
import os, psycopg2
try:
    psycopg2.connect(
        host=os.environ.get("DB_HOST","db"),
        port=os.environ.get("DB_PORT","5432"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        dbname=os.environ.get("DB_NAME"),
    ).close()
except Exception:
    raise SystemExit(1)
PY
done

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Посев демо-данных отключён по умолчанию, включается только переменной окружения.
if [ "${SEED_DATA:-0}" = "1" ]; then
  echo "Seeding demo data via repairs/seed_repairs.py ..."
  python - <<'PY'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE","core.settings")
django.setup()
try:
    # Позови нужную функцию из repairs/seed_repairs.py
    # если у тебя другая — поправь имя ниже.
    from repairs.seed_repairs import seed as run_seed
except Exception:
    # Если модуля или функции нет — просто выходим без ошибки.
    print("seed_repairs module or function not found, skip.")
else:
    run_seed()
    print("Seed completed.")
PY
fi

echo "Starting Gunicorn..."
exec gunicorn core.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60
