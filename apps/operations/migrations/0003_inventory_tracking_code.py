from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0002_inventory_procurement")]
    operations = [
        migrations.AddField(model_name="inventoryitem", name="tracking_code", field=models.CharField(blank=True, help_text="Barcode value or RFID tag UID; scanners can enter this code into the warehouse lookup.", max_length=100)),
    ]
