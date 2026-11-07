from django import forms
from .models import EnergyReading, Machine, MaintenanceRecord, QualityInspection, SafetyIncident, SustainabilityReading, WorkflowRule


class WorkflowRuleForm(forms.ModelForm):
    class Meta:
        model = WorkflowRule
        fields = ("name", "event", "factory", "escalation_after_hours", "is_enabled")
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories
        self.fields["factory"].required = True


class QualityInspectionForm(forms.ModelForm):
    class Meta:
        model = QualityInspection
        fields = ("factory", "department", "product_reference", "image", "length_mm", "width_mm", "height_mm", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3}), "image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"})}

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if factory and department and department.factory_id != factory.pk:
            self.add_error("department", "The department must belong to the selected factory.")
        image = cleaned.get("image")
        if image and image.size > 10 * 1024 * 1024:
            self.add_error("image", "Inspection images must be 10 MB or smaller.")
        if image and image.content_type not in ("image/jpeg", "image/png", "image/webp"):
            self.add_error("image", "Upload a JPEG, PNG, or WebP image.")
        dimensions = [cleaned.get(field) for field in ("length_mm", "width_mm", "height_mm")]
        if any(value is not None for value in dimensions) and not all(value is not None for value in dimensions):
            for field, value in zip(("length_mm", "width_mm", "height_mm"), dimensions):
                if value is None: self.add_error(field, "Enter all three dimensions or leave them blank.")
        for field, value in zip(("length_mm", "width_mm", "height_mm"), dimensions):
            if value is not None and value <= 0: self.add_error(field, "Dimension must be greater than zero.")
        return cleaned


class EnergyReadingForm(forms.ModelForm):
    class Meta:
        model = EnergyReading
        fields = ("factory", "department", "meter_reference", "reading_at", "meter_kwh", "unit_cost", "currency_code", "emission_factor", "notes")
        widgets = {"reading_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if factory and department and department.factory_id != factory.pk:
            self.add_error("department", "The department must belong to the selected factory.")
        reading = cleaned.get("meter_kwh")
        if reading is not None and reading < 0:
            self.add_error("meter_kwh", "Meter readings cannot be negative.")
        for field in ("unit_cost", "emission_factor"):
            value = cleaned.get(field)
            if value is not None and value < 0: self.add_error(field, "This value cannot be negative.")
        currency_code = cleaned.get("currency_code", "").strip()
        if currency_code and (len(currency_code) != 3 or not currency_code.isalpha()):
            self.add_error("currency_code", "Enter a three-letter currency code, such as INR or USD.")
        elif currency_code:
            cleaned["currency_code"] = currency_code.upper()
        return cleaned


class SustainabilityReadingForm(forms.ModelForm):
    class Meta:
        model = SustainabilityReading
        fields = ("factory", "department", "resource", "reading_date", "reference", "quantity", "unit_cost", "currency_code", "recycled_quantity", "notes")
        widgets = {
            "reading_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.TextInput(attrs={"maxlength": 250}),
        }

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments
        self.fields["unit_cost"].required = False
        self.fields["reference"].required = False
        self.fields["recycled_quantity"].help_text = "For waste readings only; leave at zero for electricity and water."

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        resource = cleaned.get("resource")
        quantity = cleaned.get("quantity")
        unit_cost = cleaned.get("unit_cost")
        recycled = cleaned.get("recycled_quantity") or 0
        if factory and department and department.factory_id != factory.pk:
            self.add_error("department", "The department must belong to the selected factory.")
        if quantity is not None and quantity < 0:
            self.add_error("quantity", "Consumption or waste quantity cannot be negative.")
        if unit_cost is not None and unit_cost < 0:
            self.add_error("unit_cost", "Unit cost cannot be negative.")
        currency_code = cleaned.get("currency_code", "").strip()
        if currency_code and (len(currency_code) != 3 or not currency_code.isalpha()):
            self.add_error("currency_code", "Enter a three-letter currency code, such as INR or USD.")
        elif currency_code:
            cleaned["currency_code"] = currency_code.upper()
        if resource == SustainabilityReading.Resource.WASTE and quantity is not None and recycled > quantity:
            self.add_error("recycled_quantity", "Recycled quantity cannot exceed total waste quantity.")
        if resource != SustainabilityReading.Resource.WASTE and recycled:
            self.add_error("recycled_quantity", "Recycled quantity applies only to waste readings.")
        return cleaned


class MachineForm(forms.ModelForm):
    class Meta:
        model = Machine
        fields = ("factory", "department", "production_unit", "asset_tag", "name", "manufacturer", "model_number", "serial_number", "capacity_per_hour", "purchase_date", "purchase_cost", "useful_life_months", "warranty_expiry", "replacement_date", "status")
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"}), "warranty_expiry": forms.DateInput(attrs={"type": "date"}), "replacement_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments
        self.fields["production_unit"].queryset = self.fields["production_unit"].queryset.filter(department__in=allowed_departments) if allowed_departments is not None else self.fields["production_unit"].queryset.all()

    def clean(self):
        cleaned = super().clean()
        factory, department, unit = cleaned.get("factory"), cleaned.get("department"), cleaned.get("production_unit")
        if factory and department and department.factory_id != factory.pk:
            self.add_error("department", "Department must belong to the selected factory.")
        if unit and department and unit.department_id != department.pk:
            self.add_error("production_unit", "Production unit must belong to the selected department.")
        cost, life = cleaned.get("purchase_cost"), cleaned.get("useful_life_months")
        if cost is not None and cost < 0: self.add_error("purchase_cost", "Purchase cost cannot be negative.")
        if life is not None and life < 1: self.add_error("useful_life_months", "Useful life must be at least one month.")
        return cleaned


class MaintenanceRecordForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRecord
        fields = ("machine", "kind", "status", "scheduled_for", "summary", "details", "resolution")
        widgets = {"scheduled_for": forms.DateInput(attrs={"type": "date"}), "details": forms.Textarea(attrs={"rows": 3}), "resolution": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, allowed_machines=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_machines is not None: self.fields["machine"].queryset = allowed_machines


class SafetyIncidentForm(forms.ModelForm):
    class Meta:
        model = SafetyIncident
        fields = ("factory", "department", "incident_date", "summary", "details", "severity", "status")
        widgets = {"incident_date": forms.DateInput(attrs={"type": "date"}), "details": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if factory and department and department.factory_id != factory.pk:
            self.add_error("department", "Department must belong to the selected factory.")
        return cleaned
