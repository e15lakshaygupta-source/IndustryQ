from django.conf import settings
from django.db import models


class Asset(models.Model):

    class Category(models.TextChoices):
        IT = "IT", "IT Equipment"
        FURNITURE = "FURNITURE", "Furniture"
        VEHICLE = "VEHICLE", "Vehicles"
        MACHINERY = "MACHINERY", "Machinery"
        BUILDING = "BUILDING", "Buildings"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ASSIGNED = "ASSIGNED", "Assigned"
        MAINTENANCE = "MAINTENANCE", "Under Maintenance"
        DISPOSED = "DISPOSED", "Disposed"

    asset_code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=Category.choices)
    serial_number = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    vendor = models.CharField(max_length=200, blank=True)
    warranty_expiry = models.DateField(null=True, blank=True)

    location = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=150, blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="industryq_assets"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE
    )

    useful_life_years = models.PositiveSmallIntegerField(
        null=True, blank=True
    )
    salvage_value = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.asset_code} - {self.name}"


class MaintenanceRecord(models.Model):

    class MaintenanceType(models.TextChoices):
        REPAIR = "REPAIR", "Repair"
        SERVICE = "SERVICE", "Routine Service"
        INSPECTION = "INSPECTION", "Inspection"
        OTHER = "OTHER", "Other"

    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="maintenance_records"
    )

    maintenance_type = models.CharField(
        max_length=20,
        choices=MaintenanceType.choices
    )

    maintenance_date = models.DateField()
    cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    service_provider = models.CharField(max_length=200, blank=True)
    details = models.TextField(blank=True)
    next_service_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-maintenance_date"]

    def __str__(self):
        return f"{self.asset.asset_code} - {self.maintenance_type}"
