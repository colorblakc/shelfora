# Shelfora container image: Flask served by gunicorn. The database is a separate MySQL service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHELFORA_BEHIND_PROXY=1 \
    FLASK_APP=app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
