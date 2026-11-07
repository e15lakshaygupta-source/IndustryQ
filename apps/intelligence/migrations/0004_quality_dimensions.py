from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("intelligence", "0003_sustainabilityreading")]
    operations = [
        migrations.AddField(model_name="qualityinspection", name="length_mm", field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField(model_name="qualityinspection", name="width_mm", field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField(model_name="qualityinspection", name="height_mm", field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
    ]
