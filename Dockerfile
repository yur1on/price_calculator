# Dockerfile
FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости для psycopg2 и pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev gcc \
    libjpeg62-turbo-dev zlib1g-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . /app

# Собираем статику при запуске контейнера (командой entrypoint)
CMD ["bash", "-lc", "python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn core.wsgi:application --bind 0.0.0.0:8000"]
