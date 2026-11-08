# Production deployment

This project can run behind a managed platform or a reverse proxy with Gunicorn. The repository does not select a cloud provider, domain, object-storage service, or external error-monitoring vendor; configure those in the hosting environment.

## 1. Prepare production services

- Provision PostgreSQL and a dedicated least-privilege application role. Permit connections only from the application network and require TLS.
- Provision an HTTPS-capable ingress/load balancer and a persistent media volume or private object-storage bucket. Local SQLite and ephemeral container disks are for development only.
- Point the domain's DNS records at the ingress. Issue a TLS certificate there and redirect HTTP to HTTPS at the ingress or Django.
- Configure a secret manager for application secrets and database credentials. Never commit `.env` or place production secrets in build arguments or logs.

## 2. Configure the application

Copy the variable names from `.env.example` into the host's secret/configuration store and replace every example value. Set:

- `DJANGO_DEBUG=0`, a unique random `DJANGO_SECRET_KEY` of at least 50 characters, and exact comma-separated `DJANGO_ALLOWED_HOSTS` hostnames (no scheme).
- `DJANGO_CSRF_TRUSTED_ORIGINS` to the HTTPS origins used by browser clients, including the scheme.
- `DB_ENGINE=postgresql` and the database name, user, password, host, port, and `DB_SSLMODE=require` (or the stronger mode required by the database provider).
- `SECURE_SSL_REDIRECT=1` after HTTPS termination works. The Django production default enables this redirect.
- `TRUST_PROXY_SSL_HEADER=1` only if the trusted ingress strips any incoming `X-Forwarded-Proto` supplied by clients and sets its own value. Otherwise leave it disabled and have the ingress connect to Django over HTTPS or handle redirects itself.
- `SECURE_HSTS_SECONDS=31536000` after HTTPS is stable. Enable `SECURE_HSTS_INCLUDE_SUBDOMAINS` only when every subdomain supports HTTPS; keep preload disabled unless the domain is intentionally submitted to the browser preload lists.
- `MEDIA_ROOT` on persistent storage if the platform does not persist the default directory. Keep private uploads behind the application's permission-checked download views; do not expose the upload directory as a public static directory.

`SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` are enabled automatically when `DEBUG=0`. Django logs are written to stdout/stderr; set `DJANGO_LOG_LEVEL` and configure the host to retain logs and alert on uncaught 5xx errors. No external error-monitoring service is wired into this repository yet.

## 3. Build and release

From a clean checkout/build image:

```sh
python -m pip install -r requirements-deploy.txt
python manage.py check --deploy
python manage.py migrate --check
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Start the web process from the included `Procfile`. It runs Gunicorn and sends access, error, and captured application output to the platform log stream. Set process count and memory limits for the actual instance size; the default worker count is only a starting point. Run scheduled work such as backups as a separate platform task, not in every web worker.

Before moving traffic, verify the deployment with an authenticated login, a static asset, a permitted upload/download, a denied cross-scope request, and the provider's health check. Keep `DEBUG` disabled in all production processes.

## 4. Static and uploaded files

WhiteNoise serves collected static assets from `STATIC_ROOT`; run `collectstatic` for every release and deploy the resulting files with that release. Uploaded media is separate from static files. Mount durable storage at `MEDIA_ROOT` or configure a private object-storage backend before accepting production uploads. Back up media on the same schedule as the database and test restoring both together.

## 5. Backups, recovery, and rollback

- Schedule encrypted, off-host PostgreSQL backups with retention and alerting. A typical custom-format backup is `pg_dump -Fc`; restore it to a clean recovery database with `pg_restore --no-owner --dbname=DATABASE backup.dump`.
- The application's `backup_data` command creates a database fixture plus media archive for application-level recovery. Keep its output off-host and encrypted; the archive is not a substitute for a database-native point-in-time recovery strategy on a busy production database.
- Test recovery regularly in an isolated environment. Verify database rows, uploaded files, login, and critical workflows before recording the recovery as successful. Never test restore against the live database.
- For rollback, stop or drain traffic, redeploy the previous application artifact, and restore the pre-release backup only when the release changed data incompatibly. Prefer backward-compatible expand/migrate/contract database changes so code rollback does not require data rollback. Take a fresh backup before any restore.

## 6. Operational monitoring

Collect stdout/stderr from Gunicorn and Django in the hosting platform's centralized logging service. Alert on process restarts, sustained 5xx rates, database connection failures, disk/media capacity, and missed backup jobs. Configure an external error tracker in the hosting environment if required; avoid logging passwords, session identifiers, API keys, or uploaded document contents.

## 7. Production security review

Run `python manage.py check --deploy` with the real production environment variables and review every warning against the actual ingress and TLS setup. Confirm `DEBUG=0`, exact hosts/origins, HTTPS, secure cookies, secret rotation and access, database network restrictions, private media access, dependency patching, and backup restore procedures before release.
