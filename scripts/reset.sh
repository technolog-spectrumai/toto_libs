#!/bin/bash
set -e

echo "Waiting for Postgres..."

# Wait until Postgres is ready
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER"; do
  sleep 1
done

echo "Postgres is ready."
echo "Running Django setup..."

python manage.py makemigrations
python manage.py migrate
python manage.py init_platform --password="${ADMIN_PASSWORD:-qwerty123456}" --reset
python manage.py ingress_all
python manage.py collectstatic --noinput

exec "$@"

