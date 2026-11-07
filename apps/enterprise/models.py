import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ManagedDocument(models.Model):
    class Category(models.TextChoices):
        CONTRACT = "contract", "Contract"
        INVOICE = "invoice", "Invoice"
        CERTIFICATE = "certificate", "Certificate"
        SOP = "sop", "Standard operating procedure"
        OTHER = "other", "Other"

    title = models.CharField(max_length=180)
    category = models.CharField(max_length=20, choices=Category.choices)
    company = models.ForeignKey("organization.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="managed_documents")
    factory = models.ForeignKey("organization.Factory", null=True, blank=True, on_delete=models.CASCADE, related_name="managed_documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="managed_documents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "title"]
        constraints = [models.UniqueConstraint(fields=["title", "category", "company", "factory"], name="unique_managed_document_scope")]

    def __str__(self): return self.title


class DocumentVersion(models.Model):
    document = models.ForeignKey(ManagedDocument, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    file = models.FileField(upload_to="enterprise_documents/%Y/%m/")
    notes = models.CharField(max_length=250, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [models.UniqueConstraint(fields=["document", "version_number"], name="unique_managed_document_version")]


class ComplianceItem(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETE = "complete", "Complete"
        OVERDUE = "overdue", "Overdue"

    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    title = models.CharField(max_length=180)
    requirement = models.TextField(blank=True)
    category = models.CharField(max_length=80, blank=True, help_text="Regulatory, audit, safety, environmental, or other")
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="compliance_items")
    due_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="compliance_items")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    risk_level = models.CharField(max_length=12, choices=RiskLevel.choices, default=RiskLevel.MEDIUM)
    evidence = models.FileField(upload_to="compliance_evidence/%Y/%m/", blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_compliance_items")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "-risk_level", "title"]


class IndustrialProject(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETE = "complete", "Complete"

    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="industrial_projects")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_industrial_projects")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_industrial_projects")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ProjectTask(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        DOING = "doing", "In progress"
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"

    project = models.ForeignKey(IndustrialProject, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=180)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="industrial_project_tasks")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO)
    milestone = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "created_at"]


class PortalAccess(models.Model):
    class PartyType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SUPPLIER = "supplier", "Supplier"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_portal_access")
    party_type = models.CharField(max_length=12, choices=PartyType.choices)
    customer = models.ForeignKey("operations.Customer", null=True, blank=True, on_delete=models.CASCADE, related_name="portal_accesses")
    supplier = models.ForeignKey("operations.Supplier", null=True, blank=True, on_delete=models.CASCADE, related_name="portal_accesses")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.party_type == self.PartyType.CUSTOMER and (not self.customer_id or self.supplier_id): raise ValidationError("Customer access must link one customer only.")
        if self.party_type == self.PartyType.SUPPLIER and (not self.supplier_id or self.customer_id): raise ValidationError("Supplier access must link one supplier only.")


class IntegrationApp(models.Model):
    class Provider(models.TextChoices):
        ACCOUNTING = "accounting", "Accounting system"
        ERP = "erp", "ERP platform"
        LOGISTICS = "logistics", "Logistics provider"
        BANK = "bank", "Banking / payments"
        CUSTOM = "custom", "Custom integration"

    name = models.CharField(max_length=140)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="integration_apps")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_hash = models.CharField(max_length=64)
    token_prefix = models.CharField(max_length=12)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class RecoveryTest(models.Model):
    archive_name = models.CharField(max_length=255)
    passed = models.BooleanField(default=False)
    result = models.TextField()
    tested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    tested_at = models.DateTimeField(default=timezone.now)

