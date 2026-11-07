from django.conf import settings
from django.db import models
from django.utils import timezone


class QualityInspection(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="quality_inspections")
    department = models.ForeignKey("organization.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_inspections")
    inspected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="quality_inspections")
    product_reference = models.CharField(max_length=150)
    image = models.ImageField(upload_to="quality_inspections/%Y/%m/")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    defect_label = models.CharField(max_length=150, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Optional model confidence percentage.")
    image_brightness = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    image_edge_detail = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    image_quality_flag = models.BooleanField(default=False)
    length_mm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    width_mm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    height_mm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product_reference} - {self.get_status_display()}"


class EnergyReading(models.Model):
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="energy_readings")
    department = models.ForeignKey("organization.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="energy_readings")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="energy_readings")
    meter_reference = models.CharField(max_length=100)
    reading_at = models.DateTimeField()
    meter_kwh = models.DecimalField(max_digits=14, decimal_places=3, help_text="Cumulative electricity meter reading in kWh.")
    consumption_kwh = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True, editable=False)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True, help_text="Cost per kWh applied to the consumption interval ending at this reading.")
    currency_code = models.CharField(max_length=3, default="INR", help_text="Three-letter currency code used for this reading's cost.")
    emission_factor = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, help_text="Optional kg CO₂e per kWh for the interval ending at this reading.")
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-reading_at"]
        constraints = [models.UniqueConstraint(fields=["factory", "meter_reference", "reading_at"], name="unique_energy_reading_per_meter_time")]

    def save(self, *args, **kwargs):
        previous = EnergyReading.objects.filter(factory=self.factory, meter_reference=self.meter_reference, reading_at__lt=self.reading_at).order_by("-reading_at").first()
        if previous:
            self.consumption_kwh = max(self.meter_kwh - previous.meter_kwh, 0)
        else:
            self.consumption_kwh = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.factory} / {self.meter_reference} / {self.reading_at}"

    @property
    def cost_total(self):
        return self.consumption_kwh * self.unit_cost if self.consumption_kwh is not None and self.unit_cost is not None else None

    @property
    def carbon_total(self):
        return self.consumption_kwh * self.emission_factor if self.consumption_kwh is not None and self.emission_factor is not None else None


class SustainabilityReading(models.Model):
    class Resource(models.TextChoices):
        WATER = "water", "Water"
        WASTE = "waste", "Waste"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="sustainability_readings")
    department = models.ForeignKey("organization.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="sustainability_readings")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sustainability_readings")
    resource = models.CharField(max_length=16, choices=Resource.choices)
    reading_date = models.DateField()
    reference = models.CharField(max_length=100, blank=True, help_text="Optional meter, utility account, or waste stream reference.")
    quantity = models.DecimalField(max_digits=16, decimal_places=3, help_text="Period consumption: m³ for water or kg for waste.")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True, help_text="Cost per m³ or kg in your configured currency.")
    currency_code = models.CharField(max_length=3, default="INR", help_text="Three-letter currency code used for this reading's cost.")
    recycled_quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0, help_text="For waste only: quantity recycled or diverted from disposal, in kg.")
    notes = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reading_date", "resource", "factory__name"]
        indexes = [models.Index(fields=["resource", "reading_date"])]

    @property
    def unit_label(self):
        return {self.Resource.WATER: "m³", self.Resource.WASTE: "kg"}.get(self.resource, "units")

    @property
    def cost_total(self):
        return self.quantity * self.unit_cost if self.unit_cost is not None else None

    @property
    def __str__(self):
        return f"{self.get_resource_display()} / {self.factory} / {self.reading_date}"


class AssistantQuery(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_queries")
    question = models.CharField(max_length=500)
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class WorkflowRule(models.Model):
    class Event(models.TextChoices):
        LOW_STOCK = "low_stock", "Stock at or below minimum"
        LATE_DELIVERY = "late_delivery", "Purchase order past expected date"
        MAINTENANCE_DUE = "maintenance_due", "Maintenance due"
        QUALITY_REVIEW = "quality_review", "Inspection awaiting review"
        LEAVE_APPROVAL = "leave_approval", "Leave awaiting approval"

    name = models.CharField(max_length=140)
    event = models.CharField(max_length=24, choices=Event.choices)
    factory = models.ForeignKey("organization.Factory", null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_rules")
    escalation_after_hours = models.PositiveSmallIntegerField(default=24)
    is_enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="workflow_rules")
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WorkflowAlert(models.Model):
    rule = models.ForeignKey(WorkflowRule, on_delete=models.CASCADE, related_name="alerts")
    fingerprint = models.CharField(max_length=180)
    message = models.CharField(max_length=500)
    target_url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["rule", "fingerprint"], name="unique_workflow_alert_per_event")]


class Machine(models.Model):
    class Status(models.TextChoices):
        OPERATIONAL = "operational", "Operational"
        MAINTENANCE = "maintenance", "Maintenance due"
        BREAKDOWN = "breakdown", "Breakdown"
        INACTIVE = "inactive", "Inactive"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="machines")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="machines")
    production_unit = models.ForeignKey("organization.ProductionUnit", null=True, blank=True, on_delete=models.SET_NULL, related_name="machines")
    asset_tag = models.CharField(max_length=60)
    name = models.CharField(max_length=150)
    manufacturer = models.CharField(max_length=120, blank=True)
    model_number = models.CharField(max_length=120, blank=True)
    serial_number = models.CharField(max_length=120, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    capacity_per_hour = models.PositiveIntegerField(null=True, blank=True, help_text="Rated output units per hour, used by the digital twin.")
    useful_life_months = models.PositiveSmallIntegerField(null=True, blank=True)
    warranty_expiry = models.DateField(null=True, blank=True)
    replacement_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPERATIONAL)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["factory__name", "asset_tag"]
        constraints = [models.UniqueConstraint(fields=["factory", "asset_tag"], name="unique_machine_asset_per_factory")]

    def __str__(self):
        return f"{self.asset_tag} - {self.name}"

    @property
    def monthly_depreciation(self):
        if not self.purchase_cost or not self.useful_life_months:
            return None
        return self.purchase_cost / self.useful_life_months

    @property
    def accumulated_depreciation(self):
        if self.purchase_cost is None or not self.useful_life_months or not self.purchase_date:
            return None
        today = timezone.localdate()
        months = max(0, (today.year - self.purchase_date.year) * 12 + today.month - self.purchase_date.month)
        depreciated = self.monthly_depreciation * min(months, self.useful_life_months)
        return min(self.purchase_cost, depreciated).quantize(self.purchase_cost.__class__("0.01"))

    @property
    def book_value_estimate(self):
        if self.accumulated_depreciation is None:
            return None
        return self.purchase_cost - self.accumulated_depreciation


class MaintenanceRecord(models.Model):
    class Kind(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled maintenance"
        BREAKDOWN = "breakdown", "Breakdown"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="maintenance_records")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.SCHEDULED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    scheduled_for = models.DateField(null=True, blank=True)
    summary = models.CharField(max_length=180)
    details = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="machine_maintenance_records")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.machine.asset_tag}: {self.summary}"


class SafetyIncident(models.Model):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"

    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="safety_incidents")
    department = models.ForeignKey("organization.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="safety_incidents")
    incident_date = models.DateField()
    summary = models.CharField(max_length=180)
    details = models.TextField()
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.LOW)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reported_safety_incidents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-incident_date", "-created_at"]

    def __str__(self):
        return self.summary
