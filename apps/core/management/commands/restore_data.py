import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import UserActivity
from apps.core.backup_utils import create_backup, stage_backup


class Command(BaseCommand):
    help = "Restore a database and uploaded-media backup after explicit confirmation."

    def add_arguments(self, parser):
        parser.add_argument("archive", help="Path to a trusted INDUSTRIQ backup archive.")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm that the configured database will be replaced.",
        )
        parser.add_argument(
            "--backup-dir",
            default=str(settings.BASE_DIR / "backups"),
            help="Directory for the automatic pre-restore safety backup.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Restore is destructive; rerun with --yes after verifying the archive.")
        archive_path = Path(options["archive"]).resolve()
        if not archive_path.is_file():
            raise CommandError(f"Backup archive does not exist: {archive_path}")

        try:
            with tempfile.TemporaryDirectory(prefix="industryos-restore-") as temporary_directory:
                database_path, staged_media_root, manifest = stage_backup(
                    archive_path,
                    temporary_directory,
                )
                safety_backup = create_backup(
                    output_dir=Path(options["backup_dir"]),
                    retention_days=None,
                )
                self.stdout.write(f"Pre-restore safety backup: {safety_backup}")
                call_command("flush", interactive=False, verbosity=0)
                call_command("loaddata", str(database_path), verbosity=0)

                media_root = Path(settings.MEDIA_ROOT)
                media_root.mkdir(parents=True, exist_ok=True)
                if staged_media_root.exists():
                    for source in staged_media_root.rglob("*"):
                        if source.is_file():
                            relative_path = source.relative_to(staged_media_root)
                            destination = media_root / relative_path
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)

                UserActivity.objects.create(
                    user=None,
                    action="backup_restored",
                    details={
                        "archive": archive_path.name,
                        "created_at": manifest.get("created_at", ""),
                        "pre_restore_backup": str(safety_backup),
                    },
                )
        except (OSError, ValueError) as error:
            raise CommandError(f"Restore could not be completed: {error}") from error

        self.stdout.write(self.style.SUCCESS(f"Backup restored from: {archive_path}"))
