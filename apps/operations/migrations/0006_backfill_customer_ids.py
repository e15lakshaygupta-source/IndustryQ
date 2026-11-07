from django.db import migrations


def backfill_customer_ids(apps, schema_editor):
    Customer = apps.get_model("operations", "Customer")
    for customer in Customer.objects.filter(customer_id="").iterator():
        customer.customer_id = f"CUST-{customer.company_id}-{customer.pk:05d}"
        customer.save(update_fields=["customer_id"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0005_alter_billingdocument_options_and_more")]
    operations = [migrations.RunPython(backfill_customer_ids, migrations.RunPython.noop)]
