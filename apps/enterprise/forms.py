from django import forms
from django.contrib.auth import get_user_model
from apps.operations.models import Customer, Supplier
from apps.organization.models import Company, Factory
from .models import ComplianceItem, IndustrialProject, IntegrationApp, ManagedDocument, PortalAccess, ProjectTask


class DocumentUploadForm(forms.Form):
    title = forms.CharField(max_length=180)
    category = forms.ChoiceField(choices=ManagedDocument.Category.choices)
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False)
    factory = forms.ModelChoiceField(queryset=Factory.objects.none(), required=False)
    file = forms.FileField()
    notes = forms.CharField(max_length=250, required=False)
    def __init__(self, *args, companies=None, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is not None: self.fields["company"].queryset = companies
        if factories is not None: self.fields["factory"].queryset = factories
    def clean(self):
        cleaned = super().clean()
        factory, company = cleaned.get("factory"), cleaned.get("company")
        if factory and company and factory.company_id != company.pk: self.add_error("factory", "Choose a factory belonging to the selected company.")
        if factory and not company: cleaned["company"] = factory.company
        if not factory and not company: self.add_error("factory", "Choose a company or factory scope.")
        return cleaned
    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 25 * 1024 * 1024: raise forms.ValidationError("Documents must be 25 MB or smaller.")
        return upload


class ComplianceItemForm(forms.ModelForm):
    class Meta:
        model = ComplianceItem
        fields = ("title", "requirement", "category", "factory", "due_date", "owner", "status", "risk_level", "evidence", "notes")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"}), "requirement": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories
        self.fields["owner"].queryset = get_user_model().objects.filter(is_active=True)


class IndustrialProjectForm(forms.ModelForm):
    class Meta:
        model = IndustrialProject
        fields = ("name", "description", "factory", "manager", "start_date", "due_date", "budget", "status")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "due_date": forms.DateInput(attrs={"type": "date"}), "description": forms.Textarea(attrs={"rows": 2})}
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories
        self.fields["manager"].queryset = get_user_model().objects.filter(is_active=True)


class ProjectTaskForm(forms.ModelForm):
    class Meta:
        model = ProjectTask
        fields = ("project", "title", "assignee", "due_date", "status", "milestone")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["project"].queryset = IndustrialProject.objects.filter(factory__in=factories)
        self.fields["assignee"].queryset = get_user_model().objects.filter(is_active=True)
    def clean_project(self):
        project = self.cleaned_data["project"]
        if not IndustrialProject.objects.filter(pk=project.pk, factory__in=self.fields["project"].queryset.values("factory_id")).exists():
            raise forms.ValidationError("Choose a project in your workspace.")
        return project


class IntegrationAppForm(forms.ModelForm):
    class Meta:
        model = IntegrationApp
        fields = ("name", "provider", "factory")
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories


class PortalAccessForm(forms.ModelForm):
    class Meta:
        model = PortalAccess
        fields = ("user", "party_type", "customer", "supplier", "is_active")
    def __init__(self, *args, factories=None, companies=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = get_user_model().objects.filter(is_active=True)
        if factories is not None:
            self.fields["supplier"].queryset = Supplier.objects.filter(factory__in=factories)
        if companies is not None:
            self.fields["customer"].queryset = Customer.objects.filter(company__in=companies)
        self.fields["user"].queryset = self.fields["user"].queryset.exclude(is_superuser=True)
        party_type = self.data.get("party_type") if self.is_bound else self.initial.get("party_type", getattr(self.instance, "party_type", ""))
        if party_type == PortalAccess.PartyType.CUSTOMER:
            self.fields["supplier"].required = False
        elif party_type == PortalAccess.PartyType.SUPPLIER:
            self.fields["customer"].required = False
    def clean(self):
        cleaned = super().clean()
        party_type = cleaned.get("party_type")
        if party_type == PortalAccess.PartyType.CUSTOMER:
            if self.instance.pk and self.instance.party_type == PortalAccess.PartyType.CUSTOMER:
                cleaned["customer"] = cleaned.get("customer") or self.instance.customer
            cleaned["supplier"] = None
            if not cleaned.get("customer"):
                self.add_error("customer", "Choose the linked customer account.")
        elif party_type == PortalAccess.PartyType.SUPPLIER:
            if self.instance.pk and self.instance.party_type == PortalAccess.PartyType.SUPPLIER:
                cleaned["supplier"] = cleaned.get("supplier") or self.instance.supplier
            cleaned["customer"] = None
            if not cleaned.get("supplier"):
                self.add_error("supplier", "Choose the linked supplier account.")
        if cleaned.get("user") and cleaned["user"].is_superuser:
            self.add_error("user", "Choose a non-administrator account for external portal access.")
        return cleaned
