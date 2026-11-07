import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intelligence", "0001_initial"),
        ("organization", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Machine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_tag", models.CharField(max_length=60)),
                ("name", models.CharField(max_length=150)),
                ("manufacturer", models.CharField(blank=True, max_length=120)),
                ("model_number", models.CharField(blank=True, max_length=120)),
                ("serial_number", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(choices=[("operational", "Operational"), ("maintenance", "Maintenance due"), ("breakdown", "Breakdown"), ("inactive", "Inactive")], default="operational", max_length=16)),
                ("registered_at", models.DateTimeField(auto_now_add=True)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="machines", to="organization.department")),
                ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="machines", to="organization.factory")),
                ("production_unit", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="machines", to="organization.productionunit")),
            ],
            options={"ordering": ["factory__name", "asset_tag"]},
        ),
        migrations.AddConstraint(
            model_name="machine",
            constraint=models.UniqueConstraint(fields=("factory", "asset_tag"), name="unique_machine_asset_per_factory"),
        ),
        migrations.CreateModel(
            name="MaintenanceRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("scheduled", "Scheduled maintenance"), ("breakdown", "Breakdown")], default="scheduled", max_length=12)),
                ("status", models.CharField(choices=[("open", "Open"), ("in_progress", "In progress"), ("completed", "Completed")], default="open", max_length=16)),
                ("scheduled_for", models.DateField(blank=True, null=True)),
                ("summary", models.CharField(max_length=180)),
                ("details", models.TextField(blank=True)),
                ("resolution", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("machine", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="maintenance_records", to="intelligence.machine")),
                ("reported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="machine_maintenance_records", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SafetyIncident",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("incident_date", models.DateField()),
                ("summary", models.CharField(max_length=180)),
                ("details", models.TextField()),
                ("severity", models.CharField(choices=[("low", "Low"), ("moderate", "Moderate"), ("high", "High"), ("critical", "Critical")], default="low", max_length=12)),
                ("status", models.CharField(choices=[("open", "Open"), ("investigating", "Investigating"), ("resolved", "Resolved")], default="open", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="safety_incidents", to="organization.department")),
                ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="safety_incidents", to="organization.factory")),
                ("reported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reported_safety_incidents", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-incident_date", "-created_at"]},
        ),
    ]
