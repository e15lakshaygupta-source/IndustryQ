from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Lead(models.Model):
    class Stage(models.TextChoices):
        NEW = "new", "New"
        QUALIFIED = "qualified", "Qualified"
        PROPOSAL = "proposal", "Proposal"
        NEGOTIATION = "negotiation", "Negotiation"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    factory = models.ForeignKey(
        "organization.Factory", on_delete=models.CASCADE, related_name="crm_leads"
    )
    customer = models.ForeignKey(
        "operations.Customer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="crm_leads",
    )
    company_name = models.CharField(max_length=180)
    contact_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=80, blank=True)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expected_close_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="owned_crm_leads",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_crm_leads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "company_name"]
        indexes = [models.Index(fields=["factory", "stage"], name="crm_lead_factory_stage_idx")]

    def __str__(self):
        return f"{self.company_name} — {self.contact_name}"


class Enquiry(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        QUOTED = "quoted", "Quoted"
        CLOSED = "closed", "Closed"

    factory = models.ForeignKey(
        "organization.Factory", on_delete=models.CASCADE, related_name="crm_enquiries"
    )
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="enquiries")
    subject = models.CharField(max_length=180)
    details = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    received_at = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_crm_enquiries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-created_at"]

    def __str__(self):
        return self.subject


class Quotation(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    factory = models.ForeignKey(
        "organization.Factory", on_delete=models.CASCADE, related_name="crm_quotations"
    )
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name="quotations")
    number = models.CharField(max_length=40)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_crm_quotations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["factory", "number"], name="crm_quote_number_per_factory"),
        ]

    @property
    def total(self):
        return sum((line.total for line in self.lines.all()), Decimal("0.00"))

    def __str__(self):
        return self.number


class QuotationLine(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=240)
    product = models.ForeignKey(
        "operations.Product", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="crm_quotation_lines",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    tax_rate = models.DecimalField(
        max_digits=6, decimal_places=3, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_quote_line_quantity_positive"),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name="crm_quote_line_tax_valid"),
        ]

    @property
    def total(self):
        subtotal = self.quantity * self.unit_price
        return (subtotal + subtotal * self.tax_rate / Decimal("100")).quantize(Decimal("0.01"))

    def __str__(self):
        return self.description


class FollowUp(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        COMPLETED = "completed", "Completed"

    factory = models.ForeignKey(
        "organization.Factory", on_delete=models.CASCADE, related_name="crm_follow_ups"
    )
    lead = models.ForeignKey(
        Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="follow_ups"
    )
    customer = models.ForeignKey(
        "operations.Customer", null=True, blank=True, on_delete=models.CASCADE,
        related_name="crm_follow_ups",
    )
    subject = models.CharField(max_length=180)
    notes = models.TextField(blank=True)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_crm_follow_ups",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_crm_follow_ups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "due_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(lead__isnull=False) | Q(customer__isnull=False),
                name="crm_follow_up_has_contact",
            ),
        ]

    def __str__(self):
        return self.subject
