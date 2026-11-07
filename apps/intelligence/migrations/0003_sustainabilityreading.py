import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intelligence", "0002_machines_maintenance_safety"),
        ("organization", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="energyreading",
            name="unit_cost",
            field=models.DecimalField(blank=True, decimal_places=4, help_text="Cost per kWh applied to the consumption interval ending at this reading.", max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="energyreading",
            name="currency_code",
            field=models.CharField(default="INR", help_text="Three-letter currency code used for this reading's cost.", max_length=3),
        ),
        migrations.AddField(
            model_name="energyreading",
            name="emission_factor",
            field=models.DecimalField(blank=True, decimal_places=6, help_text="Optional kg CO₂e per kWh for the interval ending at this reading.", max_digits=12, null=True),
        ),
        migrations.CreateModel(
            name="SustainabilityReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource", models.CharField(choices=[("water", "Water"), ("waste", "Waste")], max_length=16)),
                ("reading_date", models.DateField()),
                ("reference", models.CharField(blank=True, help_text="Optional meter, utility account, or waste stream reference.", max_length=100)),
                ("quantity", models.DecimalField(decimal_places=3, help_text="Period consumption: m³ for water or kg for waste.", max_digits=16)),
                ("unit_cost", models.DecimalField(blank=True, decimal_places=4, help_text="Cost per m³ or kg in your configured currency.", max_digits=14, null=True)),
                ("currency_code", models.CharField(default="INR", help_text="Three-letter currency code used for this reading's cost.", max_length=3)),
                ("recycled_quantity", models.DecimalField(decimal_places=3, default=0, help_text="For waste only: quantity recycled or diverted from disposal, in kg.", max_digits=16)),
                ("notes", models.CharField(blank=True, max_length=250)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sustainability_readings", to="organization.department")),
                ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sustainability_readings", to="organization.factory")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sustainability_readings", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-reading_date", "resource", "factory__name"]},
        ),
        migrations.AddIndex(
            model_name="sustainabilityreading",
            index=models.Index(fields=["resource", "reading_date"], name="int_sust_resource_91ccad_idx"),
        ),
    ]
