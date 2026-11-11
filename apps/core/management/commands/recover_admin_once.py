import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import UserActivity, UserProfile


RECOVERY_MARKER = "one_time_admin_recovery_consumed"


class Command(BaseCommand):
    help = "Create or reset one named Django superuser once, using temporary deployment environment variables."

    def handle(self, *args, **options):
        with transaction.atomic():
            if UserActivity.objects.filter(action=RECOVERY_MARKER).exists():
                self.stdout.write("One-time admin recovery was already consumed; no changes made.")
                return

            username = os.getenv("INDUSTRIQ_ADMIN_RECOVERY_USERNAME", "").strip()
            email = os.getenv("INDUSTRIQ_ADMIN_RECOVERY_EMAIL", "").strip()
            password = os.getenv("INDUSTRIQ_ADMIN_RECOVERY_PASSWORD", "")
            if not any((username, email, password)):
                self.stdout.write("Admin recovery is not enabled; no changes made.")
                return
            if not all((username, email, password)):
                raise CommandError(
                    "Set INDUSTRIQ_ADMIN_RECOVERY_USERNAME, "
                    "INDUSTRIQ_ADMIN_RECOVERY_EMAIL, and "
                    "INDUSTRIQ_ADMIN_RECOVERY_PASSWORD together."
                )

            User = get_user_model()
            try:
                validate_password(password)
            except ValidationError as error:
                raise CommandError("Recovery password rejected: " + " ".join(error.messages)) from error

            user = User.objects.select_for_update().filter(username=username).first()
            if user and not user.is_superuser:
                raise CommandError(
                    "That username already belongs to a non-superuser. Choose a new recovery username."
                )

            if user:
                user.email = email
                user.is_active = True
                user.is_staff = True
                user.is_superuser = True
                user.set_password(password)
                user.save(update_fields=["email", "is_active", "is_staff", "is_superuser", "password"])
                operation = "password_reset"
            else:
                user = User.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password,
                )
                operation = "account_created"

            UserProfile.objects.update_or_create(
                user=user,
                defaults={"role": UserProfile.Role.SUPER_ADMIN},
            )
            UserActivity.objects.create(
                user=user,
                action=RECOVERY_MARKER,
                details={"username": username, "operation": operation},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Admin recovery completed ({operation}) for username {username}. "
            "Remove the recovery environment variables from the hosting service now."
        ))
