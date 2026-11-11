web: python manage.py recover_admin_once && gunicorn config.wsgi:application --workers 3 --timeout 60 --access-logfile - --error-logfile - --capture-output
