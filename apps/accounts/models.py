from django.conf import settings
from django.db import models


class UserProfile(models.Model):

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        COMPANY_ADMIN = "company_admin", "Company Admin"
        FACTORY_MANAGER = "factory_manager", "Factory Manager"
        DEPARTMENT_MANAGER = "department_manager", "Department Manager"
        EMPLOYEE = "employee", "Employee"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile"
    )

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.EMPLOYEE
    )

    factory_name = models.CharField(max_length=150, blank=True)
    department_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    bio = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    factory = models.ForeignKey(
        "organization.Factory", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="user_profiles"
    )
    department = models.ForeignKey(
        "organization.Department", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="user_profiles"
    )
    company = models.ForeignKey(
        "organization.Company", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="user_profiles"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class UserActivity(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="activity_events"
    )
    action = models.CharField(max_length=100)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.user or 'Deleted user'} - {self.created_at}"


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=180)
    message = models.TextField()
    target_url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.user})"
