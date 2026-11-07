import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0001_initial"),
        ("organization", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(name="InventoryItem", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("sku", models.CharField(max_length=60)), ("name", models.CharField(max_length=180)),
            ("kind", models.CharField(choices=[("raw", "Raw material"), ("finished", "Finished goods")], default="raw", max_length=12)),
            ("unit_of_measure", models.CharField(default="units", max_length=30)),
            ("minimum_stock", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
            ("standard_cost", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
            ("is_active", models.BooleanField(default=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_items", to="organization.factory")),
        ], options={"ordering": ["name"]}),
        migrations.AddConstraint(model_name="inventoryitem", constraint=models.UniqueConstraint(fields=("factory", "sku"), name="unique_inventory_sku_per_factory")),
        migrations.CreateModel(name="Warehouse", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("code", models.CharField(max_length=40)), ("name", models.CharField(max_length=120)),
            ("location", models.CharField(blank=True, max_length=180)), ("is_active", models.BooleanField(default=True)),
            ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="warehouses", to="organization.factory")),
        ], options={"ordering": ["name"]}),
        migrations.AddConstraint(model_name="warehouse", constraint=models.UniqueConstraint(fields=("factory", "code"), name="unique_warehouse_code_per_factory")),
        migrations.CreateModel(name="Supplier", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=160)), ("contact_name", models.CharField(blank=True, max_length=120)),
            ("email", models.EmailField(blank=True, max_length=254)), ("phone", models.CharField(blank=True, max_length=30)),
            ("address", models.TextField(blank=True)), ("is_active", models.BooleanField(default=True)),
            ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="suppliers", to="organization.factory")),
        ], options={"ordering": ["name"]}),
        migrations.AddConstraint(model_name="supplier", constraint=models.UniqueConstraint(fields=("factory", "name"), name="unique_supplier_name_per_factory")),
        migrations.CreateModel(name="ProcurementDocument", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("number", models.CharField(max_length=60)),
            ("kind", models.CharField(choices=[("requisition", "Purchase requisition"), ("order", "Purchase order")], default="requisition", max_length=12)),
            ("status", models.CharField(choices=[("draft", "Draft"), ("approved", "Approved"), ("ordered", "Ordered"), ("part_received", "Part received"), ("received", "Received"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
            ("quantity", models.DecimalField(decimal_places=3, max_digits=14)), ("expected_date", models.DateField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("notes", models.TextField(blank=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="procurement_documents", to=settings.AUTH_USER_MODEL)),
            ("factory", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="procurement_documents", to="organization.factory")),
            ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="procurement_documents", to="operations.inventoryitem")),
            ("supplier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="procurement_documents", to="operations.supplier")),
            ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="procurement_documents", to="operations.warehouse")),
        ], options={"ordering": ["-created_at"]}),
        migrations.AddConstraint(model_name="procurementdocument", constraint=models.UniqueConstraint(fields=("factory", "number"), name="unique_procurement_number_per_factory")),
        migrations.CreateModel(name="StockMovement", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("kind", models.CharField(choices=[("receipt", "Goods receipt"), ("consumption", "Material consumption"), ("adjust_in", "Stock adjustment in"), ("adjust_out", "Stock adjustment out")], max_length=12)),
            ("quantity", models.DecimalField(decimal_places=3, max_digits=14)), ("unit_cost", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
            ("reference", models.CharField(blank=True, max_length=100)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("notes", models.CharField(blank=True, max_length=250)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="stock_movements", to=settings.AUTH_USER_MODEL)),
            ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="operations.inventoryitem")),
            ("procurement_document", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="receipts", to="operations.procurementdocument")),
            ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="operations.warehouse")),
        ], options={"ordering": ["-created_at"]}),
    ]
