import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import UserActivity, UserProfile


class ExecutiveDashboardUiTests(TestCase):
    def test_executive_centre_renders_responsive_dashboard_for_system_admin(self):
        admin = get_user_model().objects.create_superuser(
            username="executive-admin",
            email="executive-admin@example.com",
            password="Secure-Executive-Password-824!",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("core:executive_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Executive centre")
        self.assertContains(response, "Sales &amp; billing")
        self.assertContains(response, "Factory comparison")
        self.assertContains(response, "data-finance-chart")
        self.assertContains(response, "min-width:580px")
        self.assertEqual(admin.profile.role, UserProfile.Role.SUPER_ADMIN)


class BackupRecoveryTests(TransactionTestCase):
    def test_backup_and_confirmed_restore_round_trip_database_and_media(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media_root = root / "media"
            backup_dir = root / "backups"
            safety_dir = root / "safety"
            with override_settings(MEDIA_ROOT=media_root):
                user_model = get_user_model()
                original_user = user_model.objects.create_user(
                    username="restore-original",
                    password="test-password",
                )
                media_file = media_root / "documents" / "invoice.txt"
                media_file.parent.mkdir(parents=True)
                media_file.write_text("original media", encoding="utf-8")

                call_command("backup_data", output_dir=str(backup_dir))
                archive = next(backup_dir.glob("industryos-backup-*.zip"))

                user_model.objects.create_user(username="restore-discarded")
                media_file.write_text("changed media", encoding="utf-8")
                call_command(
                    "restore_data",
                    str(archive),
                    yes=True,
                    backup_dir=str(safety_dir),
                    verbosity=0,
                )

                self.assertTrue(user_model.objects.filter(pk=original_user.pk).exists())
                self.assertFalse(user_model.objects.filter(username="restore-discarded").exists())
                self.assertEqual(media_file.read_text(encoding="utf-8"), "original media")
                self.assertEqual(len(list(safety_dir.glob("industryos-backup-*.zip"))), 1)
                self.assertTrue(
                    UserActivity.objects.filter(action="backup_restored").exists()
                )

    def test_restore_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_model = get_user_model()
            user_model.objects.create_user(username="restore-guard")
            with self.assertRaisesMessage(CommandError, "rerun with --yes"):
                call_command(
                    "restore_data",
                    str(Path(temporary_directory) / "missing.zip"),
                )
            self.assertTrue(user_model.objects.filter(username="restore-guard").exists())
