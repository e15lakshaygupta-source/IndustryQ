# Deployment and recovery

## PostgreSQL setup

- Create a PostgreSQL database and least-privilege application role. Install `requirements-deploy.txt`.
- Set variables from `.env.example` in the host's secret manager. Use a random `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, exact public hostnames, and PostgreSQL credentials.
- Configure HTTPS termination and trusted proxy headers before enabling `SECURE_SSL_REDIRECT`. Require TLS for PostgreSQL when supported.
- Deploy, run `python manage.py migrate`, then `python manage.py collectstatic --noinput`. Persist uploaded media or use backed-up object storage.
- Start using the included Gunicorn `Procfile`; configure health checks, logs, TLS, and process supervision with the platform.
- Review `python manage.py check --deploy` against the actual proxy, HTTPS, and hostname configuration before release.

## Security and performance

Use environment secrets, least-privilege database credentials, HTTPS, secure cookies, exact `ALLOWED_HOSTS`, network restrictions, access-controlled uploads, and current dependencies. Review role scopes and audit activity. Configure host-level throttling and image scanning appropriate to the deployment.

For performance, monitor query timings, add indexes based on measured slow queries, and cache only non-sensitive aggregates with explicit invalidation. Never cache role-scoped responses across users.

## Backup and recovery

For PostgreSQL, schedule encrypted off-host `pg_dump -Fc` backups. Restore into a clean database with `pg_restore --clean --if-exists --no-owner --dbname=DATABASE backup.dump`. Test restores regularly. Back up uploaded media separately and validate database plus media recovery together.

For local SQLite development, use SQLite's `.backup` command or a consistent filesystem snapshot; do not rely on copying a live database file. Keep secrets and database dumps out of version control. Define retention and recovery objectives and exercise restore.

## Report coverage

Executive, workforce attendance, quality inspection, and energy reports have data sources and CSV/PDF exports. Production, inventory, and machine report pages state that their source models do not exist yet and do not fabricate results.
