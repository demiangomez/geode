#!/bin/bash
set -e

echo "Running Django migrations..."
cd backend_django_project && python manage.py migrate

echo "Starting supervisord..."
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf