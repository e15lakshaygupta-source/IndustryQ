from django import forms
from .models import Company, Factory, Department, ProductionUnit


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ("name", "registration_number", "industry_type", "email", "phone", "address", "city", "state", "country", "is_active")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class FactoryForm(forms.ModelForm):
    class Meta:
        model = Factory
        fields = ("company", "name", "factory_code", "address", "city", "state", "state_code", "gstin", "manager_name", "production_capacity", "is_active")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, allowed_companies=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_companies is not None:
            self.fields["company"].queryset = allowed_companies

    def clean(self):
        cleaned = super().clean()
        gstin = cleaned.get("gstin", "").strip().upper()
        state_code = cleaned.get("state_code", "").strip()
        if gstin and state_code and gstin[:2] != state_code:
            self.add_error("gstin", "GSTIN state code must match the factory state code.")
        cleaned["gstin"] = gstin
        return cleaned


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ("factory", "name", "department_code", "description", "manager_name", "is_active")

    def __init__(self, *args, allowed_factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None:
            self.fields["factory"].queryset = allowed_factories


class ProductionUnitForm(forms.ModelForm):
    class Meta:
        model = ProductionUnit
        fields = ("department", "name", "unit_code", "description", "is_active")

    def __init__(self, *args, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_departments is not None:
            self.fields["department"].queryset = allowed_departments
