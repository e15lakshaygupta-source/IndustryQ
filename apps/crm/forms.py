from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from apps.operations.models import Customer, Product
from .models import Enquiry, FollowUp, Lead, Quotation, QuotationLine


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = (
            "factory", "customer", "company_name", "contact_name", "email",
            "phone", "source", "stage", "estimated_value", "expected_close_date",
        )
        widgets = {
            "expected_close_date": forms.DateInput(attrs={"type": "date"}),
            "estimated_value": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }

    def __init__(self, *args, factories, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["factory"].queryset = factories
        company_ids = factories.values_list("company_id", flat=True)
        self.fields["customer"].queryset = Customer.objects.filter(
            company_id__in=company_ids, is_active=True
        )
        self.fields["customer"].required = False

    def clean(self):
        cleaned = super().clean()
        factory, customer = cleaned.get("factory"), cleaned.get("customer")
        if factory and customer and customer.company_id != factory.company_id:
            self.add_error("customer", "Customer must belong to the factory's company.")
        if cleaned.get("estimated_value") is not None and cleaned["estimated_value"] < 0:
            self.add_error("estimated_value", "Estimated value cannot be negative.")
        return cleaned


class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ("lead", "subject", "details", "status", "received_at")
        widgets = {
            "details": forms.Textarea(attrs={"rows": 4}),
            "received_at": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, leads, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lead"].queryset = leads


class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = ("lead", "valid_until", "notes")
        widgets = {
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, leads, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lead"].queryset = leads

    def clean_valid_until(self):
        valid_until = self.cleaned_data["valid_until"]
        if valid_until < timezone.localdate():
            raise forms.ValidationError("Quotation expiry date cannot be in the past.")
        return valid_until


class QuotationLineForm(forms.ModelForm):
    class Meta:
        model = QuotationLine
        fields = ("product", "description", "quantity", "unit_price", "tax_rate")
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
            "unit_price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "tax_rate": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["product"].required = False
        self.fields["tax_rate"].required = False
        self.fields["description"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("description") and cleaned.get("product"):
            cleaned["description"] = str(cleaned["product"])
        if cleaned.get("tax_rate") is None:
            cleaned["tax_rate"] = 0
        if cleaned.get("tax_rate") is not None and cleaned["tax_rate"] > 100:
            self.add_error("tax_rate", "Tax rate cannot exceed 100%.")
        if self.has_changed() and not cleaned.get("description"):
            self.add_error("description", "Choose a product or enter an item description.")
        return cleaned


QuotationLineFormSet = inlineformset_factory(
    Quotation,
    QuotationLine,
    form=QuotationLineForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ("lead", "customer", "subject", "notes", "due_at", "assigned_to")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, leads, customers, users, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lead"].queryset = leads
        self.fields["customer"].queryset = customers
        self.fields["lead"].required = False
        self.fields["customer"].required = False
        self.fields["assigned_to"].queryset = users
        self.fields["assigned_to"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("lead") and not cleaned.get("customer"):
            raise forms.ValidationError("Choose a lead or a customer for this follow-up.")
        return cleaned
