from decimal import Decimal
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.billing.validators import (
    validate_gstin,
    validate_hsn_sac,
    validate_purchase_bill_pdf,
    validate_state_code,
)


class ProductCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Product categories"

    def __str__(self): return self.name


class GSTTaxProfile(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self): return f"{self.name} ({self.code})"


class GSTTaxRate(models.Model):
    profile = models.ForeignKey(GSTTaxProfile, on_delete=models.CASCADE, related_name="rates")
    rate = models.DecimalField(max_digits=6, decimal_places=3)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "effective_from"], name="unique_gst_rate_start_per_profile"),
            models.CheckConstraint(condition=Q(rate__gte=0, rate__lte=100), name="gst_rate_between_zero_and_hundred"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")), name="gst_rate_valid_date_range"),
        ]

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "The end date must be on or after the start date."})
        overlapping = type(self).objects.filter(
            profile=self.profile,
            effective_from__lte=self.effective_to or date.max,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError("GST rate periods for a tax profile cannot overlap.")

    def __str__(self): return f"{self.profile.code}: {self.rate}% from {self.effective_from}"


class Product(models.Model):
    sku = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=180)
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    unit_of_measure = models.CharField(max_length=30, default="units")
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_category = models.CharField(max_length=40, blank=True, default="standard")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    gst_tax_profile = models.ForeignKey(GSTTaxProfile, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    hsn_sac_code = models.CharField(max_length=8, blank=True, validators=[validate_hsn_sac])
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self): return f"{self.sku} - {self.name}"

    @property
    def net_price(self):
        if self.selling_price <= 0:
            return Decimal("0.00")
        discount = self.selling_price * (self.discount_percentage / Decimal("100"))
        return self.selling_price - discount

    def save(self, *args, **kwargs):
        previous = Product.objects.filter(pk=self.pk).only("selling_price").first() if self.pk else None
        super().save(*args, **kwargs)
        if previous is None:
            if self.selling_price > 0:
                ProductPriceHistory.objects.create(product=self, old_price=Decimal("0.00"), new_price=self.selling_price, notes="Initial price entry")
            return
        if previous.selling_price != self.selling_price:
            ProductPriceHistory.objects.create(product=self, old_price=previous.selling_price, new_price=self.selling_price, notes="Price updated")


class ProductPriceHistory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history")
    old_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    new_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    effective_from = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-created_at"]

    def __str__(self): return f"{self.product.sku}: {self.old_price} -> {self.new_price}"


class ProductionPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="production_plans")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="production_plans")
    production_unit = models.ForeignKey("organization.ProductionUnit", on_delete=models.PROTECT, related_name="production_plans")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_plans")
    name = models.CharField(max_length=180)
    start_date = models.DateField()
    end_date = models.DateField()
    target_quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_production_plans")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="production_plan_valid_dates"),
            models.CheckConstraint(condition=Q(target_quantity__gt=0), name="production_plan_positive_target"),
        ]

    def __str__(self): return f"{self.name}: {self.product.sku}"


class ProductMaterialRequirement(models.Model):
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="product_material_requirements")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="material_requirements")
    raw_material = models.ForeignKey("InventoryItem", on_delete=models.PROTECT, related_name="product_requirements")
    quantity_per_unit = models.DecimalField(max_digits=14, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__name", "raw_material__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["factory", "product", "raw_material"],
                name="unique_product_material_per_factory",
            ),
            models.CheckConstraint(
                condition=Q(quantity_per_unit__gt=0),
                name="product_material_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.product.sku} requires {self.quantity_per_unit} {self.raw_material.unit_of_measure} {self.raw_material.name}"


class WorkOrder(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RELEASED = "released", "Released"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    plan = models.ForeignKey(ProductionPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="work_orders")
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="work_orders")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="work_orders")
    production_unit = models.ForeignKey("organization.ProductionUnit", on_delete=models.PROTECT, related_name="work_orders")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="work_orders")
    order_number = models.CharField(max_length=50, unique=True)
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    planned_quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_work_orders")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_start", "order_number"]
        constraints = [
            models.CheckConstraint(condition=Q(scheduled_end__gt=F("scheduled_start")), name="work_order_valid_schedule"),
            models.CheckConstraint(condition=Q(planned_quantity__gt=0), name="work_order_positive_quantity"),
        ]

    def __str__(self): return f"{self.order_number} ? {self.product.sku}"


class ProductionEntry(models.Model):
    work_order = models.ForeignKey(WorkOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries")
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="production_entries")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="production_entries")
    production_unit = models.ForeignKey("organization.ProductionUnit", on_delete=models.PROTECT, related_name="production_entries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_entries")
    production_date = models.DateField()
    actual_quantity = models.PositiveIntegerField()
    rejected_quantity = models.PositiveIntegerField(default=0)
    downtime_minutes = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="production_entries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-production_date", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(rejected_quantity__lte=F("actual_quantity")), name="rejected_not_above_actual") ]

    @property
    def good_quantity(self): return self.actual_quantity - self.rejected_quantity

    def __str__(self): return f"{self.production_date} {self.product.sku}: {self.good_quantity}"


class InventoryItem(models.Model):
    class Kind(models.TextChoices):
        RAW = "raw", "Raw material"
        FINISHED = "finished", "Finished goods"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="inventory_items")
    sku = models.CharField(max_length=60)
    tracking_code = models.CharField(max_length=100, blank=True, help_text="Barcode value or RFID tag UID; scanners can enter this code into the warehouse lookup.")
    name = models.CharField(max_length=180)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.RAW)
    unit_of_measure = models.CharField(max_length=30, default="units")
    minimum_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    standard_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["factory", "sku"], name="unique_inventory_sku_per_factory")]

    def __str__(self): return f"{self.sku} - {self.name}"


class Warehouse(models.Model):
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="warehouses")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=180, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["factory", "code"], name="unique_warehouse_code_per_factory")]

    def __str__(self): return f"{self.name} ({self.factory.name})"


class WarehouseBin(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="bins")
    code = models.CharField(max_length=60)
    description = models.CharField(max_length=180, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["warehouse__name", "code"]
        constraints = [models.UniqueConstraint(fields=["warehouse", "code"], name="unique_bin_code_per_warehouse")]

    def __str__(self): return f"{self.warehouse.code} / {self.code}"


class Supplier(models.Model):
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=15, blank=True, validators=[validate_gstin])
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True, validators=[validate_state_code])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["factory", "name"], name="unique_supplier_name_per_factory")]

    def __str__(self): return self.name

    @property
    def outstanding_balance(self):
        bills = self.bills.filter(
            kind=BillingDocument.Kind.SUPPLIER_BILL,
            status__in=[
                BillingDocument.Status.APPROVED,
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
            ],
        )
        return sum((bill.balance_due for bill in bills), Decimal("0.00"))

    @property
    def evaluation_score(self):
        scores = self.evaluations.values_list("overall_score", flat=True)
        scores = list(scores)
        return sum(scores, Decimal("0")) / len(scores) if scores else None


class SupplierEvaluation(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="evaluations")
    evaluated_on = models.DateField(default=timezone.localdate)
    quality_score = models.PositiveSmallIntegerField()
    delivery_score = models.PositiveSmallIntegerField()
    communication_score = models.PositiveSmallIntegerField()
    notes = models.TextField(blank=True)
    evaluated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="supplier_evaluations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-evaluated_on", "supplier__name"]
        constraints = [
            models.CheckConstraint(condition=Q(quality_score__gte=1, quality_score__lte=5), name="supplier_quality_score_range"),
            models.CheckConstraint(condition=Q(delivery_score__gte=1, delivery_score__lte=5), name="supplier_delivery_score_range"),
            models.CheckConstraint(condition=Q(communication_score__gte=1, communication_score__lte=5), name="supplier_communication_score_range"),
        ]

    @property
    def overall_score(self):
        return (Decimal(self.quality_score + self.delivery_score + self.communication_score) / Decimal("3")).quantize(Decimal("0.01"))


class Customer(models.Model):
    class CustomerType(models.TextChoices):
        BUSINESS = "business", "Business"
        INDIVIDUAL = "individual", "Individual"

    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="customers")
    customer_id = models.CharField(max_length=32, blank=True, editable=False)
    customer_type = models.CharField(max_length=12, choices=CustomerType.choices, default=CustomerType.BUSINESS)
    name = models.CharField(max_length=180)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    tax_registration_number = models.CharField(max_length=40, blank=True, validators=[validate_gstin])
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True, validators=[validate_state_code])
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="billing_customers")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_customer_name_per_company"),
            models.UniqueConstraint(fields=["company", "customer_id"], condition=~models.Q(customer_id=""), name="unique_customer_id_per_company"),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.customer_id:
            self.customer_id = f"CUST-{self.company_id}-{self.pk:05d}"
            type(self).objects.filter(pk=self.pk).update(customer_id=self.customer_id)

    @property
    def outstanding_balance(self):
        invoices = self.invoices.filter(
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
            ],
        )
        return sum((invoice.balance_due for invoice in invoices), Decimal("0.00"))

    def __str__(self): return self.name


class BillingDocument(models.Model):
    class Kind(models.TextChoices):
        SALES_INVOICE = "sales_invoice", "Sales invoice"
        SUPPLIER_BILL = "supplier_bill", "Supplier bill"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        ISSUED = "issued", "Issued"
        PART_PAID = "part_paid", "Partially paid"
        PAID = "paid", "Paid"
        VOID = "void", "Cancelled"

    class PaymentStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        UNPAID = "unpaid", "Unpaid"
        PART_PAID = "part_paid", "Partially paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        CANCELLED = "cancelled", "Cancelled"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="billing_documents")
    number = models.CharField(max_length=60, blank=True)
    supplier_invoice_number = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices")
    supplier = models.ForeignKey("operations.Supplier", null=True, blank=True, on_delete=models.PROTECT, related_name="bills")
    procurement_document = models.ForeignKey("operations.ProcurementDocument", null=True, blank=True, on_delete=models.SET_NULL, related_name="bills")
    supplier_invoice_pdf = models.FileField(
        upload_to="purchase_bills/%Y/%m/",
        blank=True,
        validators=[validate_purchase_bill_pdf],
    )
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=120, blank=True)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=3, default="INR")
    notes = models.TextField(blank=True)
    supplier_gstin = models.CharField(max_length=15, blank=True)
    customer_gstin = models.CharField(max_length=15, blank=True)
    place_of_supply_state_code = models.CharField(max_length=2, blank=True)
    gst_supply_type = models.CharField(max_length=12, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_billing_documents")
    approved_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="issued_billing_documents")
    issued_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="canceled_billing_documents")
    canceled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="billing_documents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["factory", "number"], name="unique_billing_number_per_factory"),
            models.UniqueConstraint(
                fields=["supplier", "supplier_invoice_number"],
                condition=Q(kind="supplier_bill") & ~Q(supplier_invoice_number=""),
                name="unique_supplier_invoice_number",
            ),
        ]
        permissions = [("manage_billing", "Can manage invoices, supplier bills, customers, and payments")]

    @property
    def balance_due(self):
        return max(self.total - self.amount_paid - self.approved_adjustment_total, 0)

    @property
    def approved_adjustment_total(self):
        return self.adjustments.filter(
            status=CreditDebitNote.Status.APPROVED
        ).aggregate(total=models.Sum("total"))["total"] or Decimal("0.00")

    @property
    def payment_status(self):
        if self.status == self.Status.DRAFT:
            return self.PaymentStatus.DRAFT
        if self.status == self.Status.APPROVED and self.kind == self.Kind.SALES_INVOICE:
            return self.PaymentStatus.APPROVED
        if self.status == self.Status.VOID:
            return self.PaymentStatus.CANCELLED
        if self.balance_due == 0 or self.status == self.Status.PAID:
            return self.PaymentStatus.PAID
        if self.due_date and self.due_date < timezone.localdate():
            return self.PaymentStatus.OVERDUE
        if self.amount_paid > 0:
            return self.PaymentStatus.PART_PAID
        return self.PaymentStatus.UNPAID

    def get_payment_status_display(self):
        return self.PaymentStatus(self.payment_status).label

    @property
    def taxable_value(self): return max(self.subtotal - self.discount_amount, 0)

    @classmethod
    def next_number(cls, factory, kind, issue_date):
        prefix = "INV" if kind == cls.Kind.SALES_INVOICE else "BILL"
        year = issue_date.strftime("%y")
        stem = f"{prefix}-{factory.factory_code}-{year}-"
        latest = cls.objects.filter(factory_id=factory.pk, kind=kind, number__startswith=stem).order_by("-number").values_list("number", flat=True).first()
        sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
        return f"{stem}{sequence:05d}"

    def __str__(self): return self.number


class BillingLine(models.Model):
    document = models.ForeignKey(BillingDocument, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=240)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="billing_lines")
    inventory_item = models.ForeignKey("operations.InventoryItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="billing_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    hsn_sac_code = models.CharField(max_length=8, blank=True, validators=[validate_hsn_sac])
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    @property
    def gross_total(self):
        base = self.taxable_value if self.document.kind == BillingDocument.Kind.SALES_INVOICE else self.line_total
        return base + self.cgst_amount + self.sgst_amount + self.igst_amount


class CreditDebitNote(models.Model):
    class Kind(models.TextChoices):
        CREDIT_NOTE = "credit_note", "Sales return / credit note"
        DEBIT_NOTE = "debit_note", "Purchase return / debit note"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        VOID = "void", "Cancelled"

    factory = models.ForeignKey(
        "organization.Factory",
        on_delete=models.CASCADE,
        related_name="credit_debit_notes",
    )
    source_document = models.ForeignKey(
        BillingDocument,
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    number = models.CharField(max_length=60)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField()
    reason = models.CharField(max_length=500)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=3, default="INR")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_credit_debit_notes",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_credit_debit_notes",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["factory", "number"], name="unique_credit_debit_note_per_factory"),
        ]

    def __str__(self):
        return self.number


class CreditDebitNoteLine(models.Model):
    note = models.ForeignKey(CreditDebitNote, on_delete=models.CASCADE, related_name="lines")
    source_line = models.ForeignKey(BillingLine, on_delete=models.PROTECT, related_name="adjustment_lines")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    hsn_sac_code = models.CharField(max_length=8, blank=True, validators=[validate_hsn_sac])
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["note", "source_line"], name="unique_source_line_per_adjustment"),
        ]

    @property
    def total(self):
        return self.taxable_value + self.cgst_amount + self.sgst_amount + self.igst_amount


class BillingPayment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    document = models.ForeignKey(BillingDocument, on_delete=models.CASCADE, related_name="payments")
    payment_date = models.DateField()
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField(max_length=30, choices=Method.choices, blank=True)
    method_details = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=250, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="billing_payments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    @property
    def receipt_number(self):
        return f"RCPT-{self.pk:08d}"


class BillingRefund(models.Model):
    payment = models.ForeignKey(BillingPayment, on_delete=models.PROTECT, related_name="refunds")
    refund_date = models.DateField()
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField(max_length=30, choices=BillingPayment.Method.choices)
    method_details = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=250)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="billing_refunds")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-refund_date", "-created_at"]

    def __str__(self):
        return f"Refund {self.amount} for {self.payment.document.number}"


class ProcurementDocument(models.Model):
    class Kind(models.TextChoices):
        REQUISITION = "requisition", "Purchase requisition"
        ORDER = "order", "Purchase order"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        ORDERED = "ordered", "Ordered"
        PART_RECEIVED = "part_received", "Part received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="procurement_documents")
    number = models.CharField(max_length=60)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.REQUISITION)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="procurement_documents")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="procurement_documents")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="procurement_documents")
    production_plan = models.ForeignKey(
        ProductionPlan,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procurement_documents",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    expected_date = models.DateField(null=True, blank=True)
    carrier = models.CharField(max_length=120, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    shipped_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="procurement_documents")
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["factory", "number"], name="unique_procurement_number_per_factory")]

    def __str__(self): return self.number


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Goods receipt"
        CONSUMPTION = "consumption", "Material consumption"
        ADJUST_IN = "adjust_in", "Stock adjustment in"
        ADJUST_OUT = "adjust_out", "Stock adjustment out"

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    bin_location = models.ForeignKey(WarehouseBin, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey("InventoryBatch", null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    procurement_document = models.ForeignKey(ProcurementDocument, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="stock_movements")
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self): return f"{self.get_kind_display()}: {self.item.sku} x {self.quantity}"


class InventoryBatch(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="batches")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="batches")
    lot_number = models.CharField(max_length=100)
    expiry_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date", "lot_number"]
        constraints = [models.UniqueConstraint(fields=["item", "warehouse", "lot_number"], name="unique_inventory_batch_per_location")]

    def __str__(self): return f"{self.item.sku} / {self.lot_number}"


class StockTransfer(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="transfers")
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="outgoing_transfers")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="incoming_transfers")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=250, blank=True)
    transferred_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    transferred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transferred_at"]
