#!/bin/sh
# Container start-up: wait for the database, create or upgrade the tables (safe to repeat),
# make sure a librarian account exists, then start the web server.
set -e

tries=0
until flask init-db; do
  tries=$((tries + 1))
  if [ "$tries" -ge 15 ]; then
    echo "The database did not become ready; giving up." >&2
    exit 1
  fi
  echo "Database not ready yet, retrying in 4 seconds..."
  sleep 4
done

flask ensure-admin

exec gunicorn wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --access-logfile -
