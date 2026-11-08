from django import forms
from django.conf import settings
from django.utils import timezone

from apps.operations.models import (
    BillingDocument,
    BillingLine,
    Customer,
    InventoryItem,
    Product,
    ProcurementDocument,
    Supplier,
)
from apps.organization.models import Factory


class SalesInvoiceForm(forms.ModelForm):
    class Meta:
        model = BillingDocument
        fields = (
            "factory",
            "customer",
            "issue_date",
            "due_date",
            "payment_terms",
            "discount_amount",
            "currency_code",
            "notes",
        )
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "payment_terms": forms.TextInput(attrs={"placeholder": "e.g. Net 30"}),
            "discount_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["factory"].queryset = factories if factories is not None else Factory.objects.all()
        self.fields["customer"].queryset = (
            Customer.objects.filter(company__factories__in=factories, is_active=True).distinct()
            if factories is not None
            else Customer.objects.filter(is_active=True)
        )
        self.fields["customer"].label_from_instance = self._customer_option_label
        self.fields["customer"].help_text = (
            "Choose the customer's verified legal record. Confirm its GSTIN and place-of-supply state before approval."
        )
        if not self.instance.pk:
            self.fields["issue_date"].initial = timezone.localdate()
            self.fields["currency_code"].initial = getattr(settings, "BILLING_DEFAULT_CURRENCY", "INR")

    @staticmethod
    def _customer_option_label(customer):
        registration = customer.tax_registration_number or "Unregistered"
        state = customer.state_code or "State code missing"
        return f"{customer.name} · {registration} · State {state}"

    def clean(self):
        cleaned = super().clean()
        factory = cleaned.get("factory")
        customer = cleaned.get("customer")
        if factory and customer and customer.company_id != factory.company_id:
            self.add_error("customer", "Customer must belong to the factory's company.")
        if cleaned.get("due_date") and cleaned.get("issue_date") and cleaned["due_date"] < cleaned["issue_date"]:
            self.add_error("due_date", "Due date cannot be before the invoice date.")
        discount = cleaned.get("discount_amount")
        if discount is not None and discount < 0:
            self.add_error("discount_amount", "Discount cannot be negative.")
        currency = cleaned.get("currency_code", "").strip()
        if len(currency) != 3 or not currency.isalpha():
            self.add_error("currency_code", "Enter a three-letter currency code.")
        else:
            cleaned["currency_code"] = currency.upper()
        return cleaned


class InvoiceCancellationForm(forms.Form):
    reason = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Explain why this invoice is being cancelled"}),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Enter a cancellation reason.")
        return reason


class CreditDebitNoteForm(forms.Form):
    issue_date = forms.DateField(
        label="Adjustment date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    reason = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Reason for the return or adjustment"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["issue_date"].initial = timezone.localdate()

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Enter a reason for this adjustment.")
        return reason


class PurchaseBillForm(forms.ModelForm):
    class Meta:
        model = BillingDocument
        fields = (
            "factory",
            "supplier",
            "supplier_invoice_number",
            "supplier_invoice_pdf",
            "procurement_document",
            "issue_date",
            "due_date",
            "payment_terms",
            "discount_amount",
            "currency_code",
            "notes",
        )
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "payment_terms": forms.TextInput(attrs={"placeholder": "e.g. Net 30"}),
            "discount_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["factory"].queryset = factories
            self.fields["supplier"].queryset = Supplier.objects.filter(
                factory__in=factories,
                is_active=True,
            ).select_related("factory")
            self.fields["procurement_document"].queryset = ProcurementDocument.objects.filter(
                factory__in=factories,
                kind=ProcurementDocument.Kind.ORDER,
            ).exclude(status=ProcurementDocument.Status.CANCELLED).select_related(
                "factory", "supplier", "item"
            )
        else:
            self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
            self.fields["procurement_document"].queryset = ProcurementDocument.objects.filter(
                kind=ProcurementDocument.Kind.ORDER,
            ).exclude(status=ProcurementDocument.Status.CANCELLED)
        self.fields["procurement_document"].required = False
        self.fields["supplier_invoice_number"].required = True
        self.fields["supplier_invoice_pdf"].required = False
        self.fields["supplier_invoice_pdf"].help_text = "Optional supplier source invoice, PDF only (10 MB maximum)."
        self.fields["procurement_document"].label = "Purchase order"
        self.fields["issue_date"].label = "Purchase invoice date"
        self.fields["issue_date"].initial = timezone.localdate()
        self.fields["currency_code"].initial = getattr(settings, "BILLING_DEFAULT_CURRENCY", "INR")

    def clean(self):
        cleaned = super().clean()
        factory = cleaned.get("factory")
        supplier = cleaned.get("supplier")
        procurement_document = cleaned.get("procurement_document")
        issue_date = cleaned.get("issue_date")
        due_date = cleaned.get("due_date")
        if factory and supplier and supplier.factory_id != factory.pk:
            self.add_error("supplier", "Supplier must belong to the selected factory.")
        if procurement_document:
            if factory and procurement_document.factory_id != factory.pk:
                self.add_error("procurement_document", "Purchase order must belong to the selected factory.")
            if supplier and procurement_document.supplier_id != supplier.pk:
                self.add_error("procurement_document", "Purchase order supplier must match the selected supplier.")
            if procurement_document.kind != ProcurementDocument.Kind.ORDER:
                self.add_error("procurement_document", "Only purchase orders can be linked to a supplier bill.")
        if due_date and issue_date and due_date < issue_date:
            self.add_error("due_date", "Due date cannot be before the purchase invoice date.")
        discount = cleaned.get("discount_amount")
        if discount is not None and discount < 0:
            self.add_error("discount_amount", "Discount cannot be negative.")
        currency = cleaned.get("currency_code", "").strip()
        if len(currency) != 3 or not currency.isalpha():
            self.add_error("currency_code", "Enter a three-letter currency code.")
        else:
            cleaned["currency_code"] = currency.upper()
        supplier_number = cleaned.get("supplier_invoice_number", "").strip()
        if supplier and supplier_number and BillingDocument.objects.filter(
            supplier=supplier,
            supplier_invoice_number__iexact=supplier_number,
            kind=BillingDocument.Kind.SUPPLIER_BILL,
        ).exclude(pk=self.instance.pk).exists():
            self.add_error("supplier_invoice_number", "This supplier invoice number is already registered.")
        cleaned["supplier_invoice_number"] = supplier_number
        return cleaned


class PurchaseBillLineForm(forms.ModelForm):
    class Meta:
        model = BillingLine
        fields = (
            "description",
            "product",
            "inventory_item",
            "quantity",
            "unit_price",
            "tax_rate",
            "hsn_sac_code",
        )
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "tax_rate": forms.NumberInput(attrs={"step": "0.001", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].required = False
        self.fields["inventory_item"].required = False
        self.fields["tax_rate"].required = False
        self.fields["product"].queryset = Product.objects.filter(is_active=True).select_related(
            "gst_tax_profile"
        )
        self.fields["inventory_item"].queryset = (
            InventoryItem.objects.filter(factory__in=factories, is_active=True)
            if factories is not None
            else InventoryItem.objects.filter(is_active=True)
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity") is not None and cleaned["quantity"] <= 0:
            self.add_error("quantity", "Quantity must be greater than zero.")
        if cleaned.get("unit_price") is not None and cleaned["unit_price"] < 0:
            self.add_error("unit_price", "Unit price cannot be negative.")
        if cleaned.get("tax_rate") is not None and not 0 <= cleaned["tax_rate"] <= 100:
            self.add_error("tax_rate", "GST rate must be between 0 and 100%.")
        if cleaned.get("inventory_item") and cleaned.get("inventory_item").factory_id not in (
            list(
                self.fields["inventory_item"].queryset.values_list("factory_id", flat=True).distinct()
            )
        ):
            self.add_error("inventory_item", "Choose an inventory item in your workspace.")
        return cleaned
