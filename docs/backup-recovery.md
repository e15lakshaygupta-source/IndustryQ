# Backup and recovery

INDUSTRIQ provides local database and uploaded-media backups through Django management commands. The backup is a portable JSON fixture plus uploaded files, checksummed and packaged in a ZIP archive. Run the commands from the project directory with the same virtual environment and settings used by the application.

## Create scheduled backups

```powershell
.\venv\Scripts\python.exe manage.py backup_data --retention-days 30
```

By default, archives are written to `backups\` under the project directory. After a successful backup, matching archives older than 30 days are removed. A custom destination can be passed with `--output-dir`.

To run this daily on Windows, create a Task Scheduler task that runs the command above once per day, using the project directory as the task's working directory. Configure the destination volume with appropriate access controls and encryption; these archives contain application data and user account hashes and are not encrypted by the command.

## Restore a backup

Restore is operator-only and replaces the configured database. Verify the archive and confirm it is from a compatible application version before running:

```powershell
.\venv\Scripts\python.exe manage.py restore_data "backups\industryos-backup-YYYYMMDDTHHMMSSZ.zip" --yes
```

The command validates archive paths and checksums, creates a pre-restore safety backup, flushes the configured database, loads the fixture, and overlays the archived media files into `MEDIA_ROOT`. Files not present in the archive are left in place. The safety backup is kept even when older than the normal retention window. Restore completion is recorded as a system activity.

Keep the application code and database migrations compatible with the backup. For PostgreSQL, configure the same `DB_ENGINE`, credentials, and database target used by the application. Test restores in a non-production environment before relying on them in a recovery event.
