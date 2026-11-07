from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.backup_utils import create_backup


class Command(BaseCommand):
    help = "Create a local database and uploaded-media backup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(settings.BASE_DIR / "backups"),
            help="Directory for backup archives (default: BASE_DIR/backups).",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=30,
            help="Remove older matching backup archives after a successful backup.",
        )

    def handle(self, *args, **options):
        if options["retention_days"] < 1:
            raise CommandError("--retention-days must be at least 1.")
        try:
            archive = create_backup(
                output_dir=Path(options["output_dir"]),
                retention_days=options["retention_days"],
            )
        except (OSError, ValueError) as error:
            raise CommandError(f"Backup failed: {error}") from error
        self.stdout.write(self.style.SUCCESS(f"Backup created: {archive}"))
