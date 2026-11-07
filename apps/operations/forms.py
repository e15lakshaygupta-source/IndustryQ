from decimal import Decimal

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from apps.organization.models import Department, Factory, ProductionUnit
from apps.billing.validators import validate_gstin, validate_state_code
from .models import BillingDocument, BillingLine, BillingPayment, BillingRefund, Customer, InventoryBatch, InventoryItem, ProcurementDocument, Product, ProductCategory, ProductMaterialRequirement, ProductionEntry, ProductionPlan, StockMovement, StockTransfer, Supplier, SupplierEvaluation, Warehouse, WarehouseBin, WorkOrder


class ProductCategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ("name", "description", "is_active")


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ("sku", "name", "category", "description", "unit_of_measure", "selling_price", "tax_category", "tax_rate", "gst_tax_profile", "hsn_sac_code", "discount_percentage", "is_active")

    def clean(self):
        cleaned = super().clean()
        selling_price = cleaned.get("selling_price")
        tax_rate = cleaned.get("tax_rate")
        discount_percentage = cleaned.get("discount_percentage")
        if selling_price is not None and selling_price < 0:
            self.add_error("selling_price", "Selling price cannot be negative.")
        if tax_rate is not None and not 0 <= tax_rate <= 100:
            self.add_error("tax_rate", "Tax rate must be between 0 and 100%.")
        if discount_percentage is not None and not 0 <= discount_percentage <= 100:
            self.add_error("discount_percentage", "Discount percentage must be between 0 and 100%.")
        return cleaned


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ("factory", "sku", "tracking_code", "name", "kind", "unit_of_measure", "minimum_stock", "standard_cost", "is_active")

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ("factory", "code", "name", "location", "is_active")

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ("factory", "name", "contact_name", "email", "phone", "address", "gstin", "state", "state_code", "is_active")

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["factory"].queryset = factories

    def clean(self):
        cleaned = super().clean()
        gstin = cleaned.get("gstin", "").strip().upper()
        state_code = cleaned.get("state_code", "").strip()
        if gstin and state_code and gstin[:2] != state_code:
            self.add_error("gstin", "GSTIN state code must match the supplier state code.")
        cleaned["gstin"] = gstin
        cleaned["state_code"] = state_code
        return cleaned


class CustomerForm(forms.ModelForm):
    customer_details_confirmed = forms.BooleanField(
        required=True,
        label="I checked these details against the customer's official business and tax records.",
        error_messages={
            "required": "Confirm the customer information was checked against official records."
        },
    )

    class Meta:
        model = Customer
        fields = ("customer_type", "name", "email", "phone", "address", "tax_registration_number", "state", "state_code", "billing_address", "shipping_address", "credit_limit", "is_active")
        labels = {
            "customer_type": "Customer type",
            "name": "Legal name / customer name",
            "address": "Registered address",
            "tax_registration_number": "GSTIN (leave blank if not registered)",
            "state": "State / Union Territory",
            "state_code": "GST state code",
            "billing_address": "Billing address (optional)",
            "shipping_address": "Shipping address (optional)",
            "credit_limit": "Credit limit",
            "is_active": "Active customer",
        }
        help_texts = {
            "name": "Enter the name exactly as shown on the customer's GST registration or official business records.",
            "address": "Enter the complete registered address. Do not use sample or placeholder details for a live invoice.",
            "tax_registration_number": "GSTIN format is checked, but registration status must be verified on the official GST portal. Leave blank for an unregistered customer; never invent a GSTIN.",
            "state": "Use the customer's actual place-of-supply state or Union Territory.",
            "state_code": "Enter the two-digit GST state code for the place of supply. It must match the GSTIN when one is provided.",
            "billing_address": "Leave blank to use the registered address.",
            "shipping_address": "Leave blank to use the registered address.",
        }
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3, "autocomplete": "street-address"}),
            "billing_address": forms.Textarea(attrs={"rows": 3}),
            "shipping_address": forms.Textarea(attrs={"rows": 3}),
            "state_code": forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{2}", "maxlength": "2"}),
            "tax_registration_number": forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "characters"}),
            "credit_limit": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["address"].required = True
        self.fields["state"].required = True
        self.fields["state_code"].required = True
        self.fields["is_active"].initial = True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("credit_limit") is not None and cleaned["credit_limit"] < 0:
            self.add_error("credit_limit", "Credit limit cannot be negative.")
        state_code = cleaned.get("state_code", "").strip()
        if state_code and (len(state_code) != 2 or not state_code.isdigit()):
            self.add_error("state_code", "Enter the two-digit GST state code.")
        elif state_code:
            cleaned["state_code"] = state_code
        try:
            validate_state_code(state_code)
        except ValidationError as error:
            self.add_error("state_code", error)
        gstin = cleaned.get("tax_registration_number", "").strip().upper()
        try:
            validate_gstin(gstin)
        except ValidationError as error:
            self.add_error("tax_registration_number", error)
        if gstin and state_code and gstin[:2] != state_code:
            self.add_error("tax_registration_number", "GSTIN state code must match the customer state code.")
        cleaned["tax_registration_number"] = gstin
        return cleaned


class BillingDocumentForm(forms.ModelForm):
    lines = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = BillingDocument
        fields = ("factory", "kind", "customer", "supplier", "procurement_document", "issue_date", "due_date", "tax_rate", "discount_amount", "currency_code", "status", "notes")
        widgets = {"issue_date": forms.DateInput(attrs={"type": "date"}), "due_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].required = False
        self.fields["supplier"].required = False
        self.fields["procurement_document"].required = False
        self.fields["procurement_document"].queryset = ProcurementDocument.objects.filter(factory__in=factories) if factories is not None else ProcurementDocument.objects.all()
        self.fields["customer"].queryset = Customer.objects.filter(company__factories__in=factories, is_active=True).distinct() if factories is not None else Customer.objects.filter(is_active=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(factory__in=factories, is_active=True) if factories is not None else Supplier.objects.filter(is_active=True)
        if factories is not None: self.fields["factory"].queryset = factories
        self.fields["currency_code"].initial = getattr(settings, "BILLING_DEFAULT_CURRENCY", "INR")
        if self.instance.pk: self.fields["lines"].initial = "[]"

    def clean(self):
        cleaned = super().clean()
        kind, factory = cleaned.get("kind"), cleaned.get("factory")
        customer, supplier = cleaned.get("customer"), cleaned.get("supplier")
        if kind == BillingDocument.Kind.SALES_INVOICE:
            if not customer: self.add_error("customer", "Choose a customer for a sales invoice.")
            if supplier: self.add_error("supplier", "Sales invoices cannot be linked to a supplier.")
            if customer and factory and customer.company_id != factory.company_id: self.add_error("customer", "Customer must belong to the factory's company.")
        elif kind == BillingDocument.Kind.SUPPLIER_BILL:
            if not supplier: self.add_error("supplier", "Choose a supplier for a supplier bill.")
            if customer: self.add_error("customer", "Supplier bills cannot be linked to a customer.")
            if supplier and factory and supplier.factory_id != factory.pk: self.add_error("supplier", "Supplier must belong to the selected factory.")
        po = cleaned.get("procurement_document")
        if po and factory and po.factory_id != factory.pk: self.add_error("procurement_document", "Purchase order must belong to the selected factory.")
        if po and supplier and po.supplier_id != supplier.pk: self.add_error("procurement_document", "The selected purchase order must belong to this supplier.")
        if po and kind == BillingDocument.Kind.SALES_INVOICE: self.add_error("procurement_document", "Purchase orders can only be linked to supplier bills.")
        if cleaned.get("tax_rate") is not None and not 0 <= cleaned["tax_rate"] <= 100: self.add_error("tax_rate", "Tax rate must be between 0 and 100%.")
        if cleaned.get("discount_amount") is not None and cleaned["discount_amount"] < 0: self.add_error("discount_amount", "Discount cannot be negative.")
        currency = cleaned.get("currency_code", "").strip()
        if len(currency) != 3 or not currency.isalpha(): self.add_error("currency_code", "Enter a three-letter currency code.")
        else: cleaned["currency_code"] = currency.upper()
        number = cleaned.get("number", "").strip()
        if number and factory and BillingDocument.objects.filter(factory=factory, number=number).exclude(pk=self.instance.pk).exists():
            self.add_error("number", "This document number is already in use for the selected factory.")
        return cleaned


class BillingLineForm(forms.ModelForm):
    class Meta:
        model = BillingLine
        fields = ("description", "product", "quantity", "unit_price", "tax_rate", "hsn_sac_code")
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "tax_rate": forms.NumberInput(attrs={"step": "0.001", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].required = False
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["tax_rate"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity") is not None and cleaned["quantity"] <= 0: self.add_error("quantity", "Quantity must be greater than zero.")
        if cleaned.get("unit_price") is not None and cleaned["unit_price"] < 0: self.add_error("unit_price", "Unit price cannot be negative.")
        if cleaned.get("tax_rate") is not None and not 0 <= cleaned["tax_rate"] <= 100:
            self.add_error("tax_rate", "GST rate must be between 0 and 100%.")
        return cleaned


class BillingPaymentForm(forms.ModelForm):
    class Meta:
        model = BillingPayment
        fields = ("payment_date", "amount", "method", "method_details", "reference", "notes")
        widgets = {"payment_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_date"].initial = timezone.localdate()
        self.fields["method"].required = True
        self.fields["method_details"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("method") == BillingPayment.Method.OTHER and not cleaned.get("method_details", "").strip():
            self.add_error("method_details", "Describe the payment method.")
        return cleaned

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0: raise forms.ValidationError("Payment amount must be greater than zero.")
        return amount


class BillingRefundForm(forms.ModelForm):
    class Meta:
        model = BillingRefund
        fields = ("payment", "refund_date", "amount", "method", "method_details", "reference", "reason")
        widgets = {
            "refund_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, invoice, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        eligible_payment_ids = []
        for payment in invoice.payments.annotate(refunded_total=Sum("refunds__amount")):
            if payment.amount > (payment.refunded_total or Decimal("0")):
                eligible_payment_ids.append(payment.pk)
        self.fields["payment"].queryset = BillingPayment.objects.filter(pk__in=eligible_payment_ids)
        self.fields["refund_date"].initial = timezone.localdate()
        self.fields["method"].required = True

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Refund amount must be greater than zero.")
        return amount

    def clean(self):
        cleaned = super().clean()
        method_details = cleaned.get("method_details", "").strip()
        if cleaned.get("method") == BillingPayment.Method.OTHER and not method_details:
            self.add_error("method_details", "Describe the refund method.")
        payment = cleaned.get("payment")
        amount = cleaned.get("amount")
        refund_date = cleaned.get("refund_date")
        if payment and refund_date and refund_date < payment.payment_date:
            self.add_error("refund_date", "Refund date cannot be before the original payment date.")
        if payment and amount is not None:
            refunded = payment.refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            if amount > payment.amount - refunded:
                self.add_error("amount", "Refund cannot exceed the remaining refundable amount for this payment.")
        if cleaned.get("reason"):
            cleaned["reason"] = cleaned["reason"].strip()
            if not cleaned["reason"]:
                self.add_error("reason", "Enter a refund reason.")
        return cleaned


class ProcurementDocumentForm(forms.ModelForm):
    class Meta:
        model = ProcurementDocument
        fields = ("factory", "number", "kind", "status", "item", "warehouse", "supplier", "quantity", "expected_date", "carrier", "tracking_number", "shipped_date", "notes")
        widgets = {"expected_date": forms.DateInput(attrs={"type": "date"}), "shipped_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["factory"].queryset = factories
            self.fields["item"].queryset = InventoryItem.objects.filter(factory__in=factories)
            self.fields["warehouse"].queryset = Warehouse.objects.filter(factory__in=factories)
            self.fields["supplier"].queryset = Supplier.objects.filter(factory__in=factories)

    def clean(self):
        cleaned = super().clean()
        factory = cleaned.get("factory")
        for name in ("item", "warehouse", "supplier"):
            obj = cleaned.get(name)
            if factory and obj and obj.factory_id != factory.pk:
                self.add_error(name, "Select a record belonging to the chosen factory.")
        if cleaned.get("quantity") is not None and cleaned["quantity"] <= 0:
            self.add_error("quantity", "Quantity must be greater than zero.")
        kind, status = cleaned.get("kind"), cleaned.get("status")
        if kind == ProcurementDocument.Kind.REQUISITION and status in (ProcurementDocument.Status.ORDERED, ProcurementDocument.Status.PART_RECEIVED, ProcurementDocument.Status.RECEIVED):
            self.add_error("status", "A requisition must be approved before it becomes a purchase order.")
        if status in (ProcurementDocument.Status.PART_RECEIVED, ProcurementDocument.Status.RECEIVED):
            self.add_error("status", "Receipt status is updated automatically when goods are received.")
        return cleaned


class ShipmentTrackingForm(forms.ModelForm):
    class Meta:
        model = ProcurementDocument
        fields = ("carrier", "tracking_number", "shipped_date", "expected_date")
        widgets = {"shipped_date": forms.DateInput(attrs={"type": "date"}), "expected_date": forms.DateInput(attrs={"type": "date"})}


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ("item", "warehouse", "kind", "quantity", "unit_cost", "procurement_document", "bin_location", "batch", "reference", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["procurement_document"].required = False
        if factories is not None:
            self.fields["item"].queryset = InventoryItem.objects.filter(factory__in=factories)
            self.fields["warehouse"].queryset = Warehouse.objects.filter(factory__in=factories)
            self.fields["bin_location"].queryset = WarehouseBin.objects.filter(warehouse__factory__in=factories, is_active=True)
            self.fields["batch"].queryset = InventoryBatch.objects.filter(item__factory__in=factories)
            self.fields["procurement_document"].queryset = ProcurementDocument.objects.filter(factory__in=factories, kind=ProcurementDocument.Kind.ORDER).exclude(status__in=[ProcurementDocument.Status.RECEIVED, ProcurementDocument.Status.CANCELLED])

    def clean(self):
        cleaned = super().clean()
        item, warehouse, document = cleaned.get("item"), cleaned.get("warehouse"), cleaned.get("procurement_document")
        if item and warehouse and item.factory_id != warehouse.factory_id:
            self.add_error("warehouse", "Warehouse must belong to the item's factory.")
        if document and item and document.item_id != item.pk:
            self.add_error("procurement_document", "The purchase order must be for the selected item.")
        if document and warehouse and document.warehouse_id != warehouse.pk:
            self.add_error("warehouse", "Goods must be received into the warehouse selected on the purchase order.")
        bin_location, batch = cleaned.get("bin_location"), cleaned.get("batch")
        if bin_location and warehouse and bin_location.warehouse_id != warehouse.pk:
            self.add_error("bin_location", "Bin must belong to the selected warehouse.")
        if batch and item and batch.item_id != item.pk:
            self.add_error("batch", "Batch must belong to the selected inventory item.")
        if batch and warehouse and batch.warehouse_id != warehouse.pk:
            self.add_error("batch", "Batch must belong to the selected warehouse.")
        if cleaned.get("quantity") is not None and cleaned["quantity"] <= 0:
            self.add_error("quantity", "Quantity must be greater than zero.")
        if cleaned.get("unit_cost") is not None and cleaned["unit_cost"] < 0:
            self.add_error("unit_cost", "Unit cost cannot be negative.")
        return cleaned


class SupplierEvaluationForm(forms.ModelForm):
    class Meta:
        model = SupplierEvaluation
        fields = ("supplier", "evaluated_on", "quality_score", "delivery_score", "communication_score", "notes")
        widgets = {"evaluated_on": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["supplier"].queryset = Supplier.objects.filter(factory__in=factories, is_active=True)
    def clean(self):
        cleaned = super().clean()
        for field in ("quality_score", "delivery_score", "communication_score"):
            score = cleaned.get(field)
            if score is not None and not 1 <= score <= 5: self.add_error(field, "Choose a score from 1 to 5.")
        return cleaned


class WarehouseBinForm(forms.ModelForm):
    class Meta:
        model = WarehouseBin
        fields = ("warehouse", "code", "description", "is_active")
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None: self.fields["warehouse"].queryset = Warehouse.objects.filter(factory__in=factories, is_active=True)


class InventoryBatchForm(forms.ModelForm):
    class Meta:
        model = InventoryBatch
        fields = ("item", "warehouse", "lot_number", "expiry_date")
        widgets = {"expiry_date": forms.DateInput(attrs={"type": "date"})}
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["item"].queryset = InventoryItem.objects.filter(factory__in=factories, is_active=True)
            self.fields["warehouse"].queryset = Warehouse.objects.filter(factory__in=factories, is_active=True)
    def clean(self):
        cleaned = super().clean()
        item, warehouse = cleaned.get("item"), cleaned.get("warehouse")
        if item and warehouse and item.factory_id != warehouse.factory_id: self.add_error("warehouse", "Warehouse must belong to the item's factory.")
        return cleaned


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ("item", "from_warehouse", "to_warehouse", "quantity", "reference", "notes")
    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["item"].queryset = InventoryItem.objects.filter(factory__in=factories, is_active=True)
            self.fields["from_warehouse"].queryset = Warehouse.objects.filter(factory__in=factories, is_active=True)
            self.fields["to_warehouse"].queryset = Warehouse.objects.filter(factory__in=factories, is_active=True)
    def clean(self):
        cleaned = super().clean()
        item, source, target = cleaned.get("item"), cleaned.get("from_warehouse"), cleaned.get("to_warehouse")
        if source and target and source.pk == target.pk: self.add_error("to_warehouse", "Choose a different destination warehouse.")
        if item and source and item.factory_id != source.factory_id: self.add_error("from_warehouse", "Source warehouse must be in the item's factory.")
        if item and target and item.factory_id != target.factory_id: self.add_error("to_warehouse", "Destination warehouse must be in the item's factory.")
        if cleaned.get("quantity") is not None and cleaned["quantity"] <= 0: self.add_error("quantity", "Quantity must be greater than zero.")
        return cleaned


class ProductMaterialRequirementForm(forms.ModelForm):
    class Meta:
        model = ProductMaterialRequirement
        fields = ("factory", "product", "raw_material", "quantity_per_unit")
        widgets = {
            "quantity_per_unit": forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
        }
        labels = {
            "factory": "Factory",
            "product": "Finished product",
            "raw_material": "Raw material",
            "quantity_per_unit": "Material required per product unit",
        }

    def __init__(self, *args, factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if factories is not None:
            self.fields["factory"].queryset = factories
            self.fields["raw_material"].queryset = InventoryItem.objects.filter(
                factory__in=factories,
                kind=InventoryItem.Kind.RAW,
                is_active=True,
            ).select_related("factory")
        self.fields["product"].queryset = Product.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        factory = cleaned.get("factory")
        material = cleaned.get("raw_material")
        quantity = cleaned.get("quantity_per_unit")
        if factory and material and material.factory_id != factory.pk:
            self.add_error("raw_material", "Choose a raw material belonging to the selected factory.")
        if quantity is not None and quantity <= 0:
            self.add_error("quantity_per_unit", "Material quantity per product unit must be greater than zero.")
        return cleaned


class ProductionPlanSelectionForm(forms.Form):
    production_plan = forms.ModelChoiceField(
        queryset=ProductionPlan.objects.none(),
        required=False,
        label="Production plan",
    )

    def __init__(self, *args, plans=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["production_plan"].queryset = plans if plans is not None else ProductionPlan.objects.none()


class RawMaterialPurchaseOrderForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.none())
    quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
    )
    expected_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, factory, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(
            factory=factory,
            is_active=True,
        )
        self.fields["warehouse"].queryset = Warehouse.objects.filter(
            factory=factory,
            is_active=True,
        )


def apply_scope(form, factories, departments, units):
    form.fields["factory"].queryset = factories
    form.fields["department"].queryset = departments
    form.fields["production_unit"].queryset = units


class ProductionPlanForm(forms.ModelForm):
    class Meta:
        model = ProductionPlan
        fields = ("factory", "department", "production_unit", "product", "name", "start_date", "end_date", "target_quantity", "status")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"})}
    def __init__(self, *args, factories=None, departments=None, units=None, **kwargs):
        super().__init__(*args, **kwargs)
        apply_scope(self, factories, departments, units)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
    def clean(self):
        data = super().clean(); validate_hierarchy(self, data); return data


class WorkOrderForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = ("plan", "factory", "department", "production_unit", "product", "order_number", "scheduled_start", "scheduled_end", "planned_quantity", "status", "notes")
        widgets = {"scheduled_start": forms.DateTimeInput(attrs={"type": "datetime-local"}), "scheduled_end": forms.DateTimeInput(attrs={"type": "datetime-local"})}
    def __init__(self, *args, factories=None, departments=None, units=None, **kwargs):
        super().__init__(*args, **kwargs)
        apply_scope(self, factories, departments, units)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["plan"].queryset = ProductionPlan.objects.filter(factory__in=factories)
    def clean(self):
        data = super().clean(); validate_hierarchy(self, data)
        plan, product, factory = data.get("plan"), data.get("product"), data.get("factory")
        if plan and product and (plan.product_id != product.pk or plan.factory_id != getattr(factory, "pk", None)):
            self.add_error("plan", "The linked plan must match the order product and factory.")
        return data


class ProductionEntryForm(forms.ModelForm):
    class Meta:
        model = ProductionEntry
        fields = ("work_order", "factory", "department", "production_unit", "product", "production_date", "actual_quantity", "rejected_quantity", "downtime_minutes", "notes")
        widgets = {"production_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, factories=None, departments=None, units=None, **kwargs):
        super().__init__(*args, **kwargs)
        apply_scope(self, factories, departments, units)
        self.fields["product"].queryset = Product.objects.all()
        self.fields["work_order"].queryset = WorkOrder.objects.filter(factory__in=factories).exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED])
    def clean(self):
        data = super().clean(); validate_hierarchy(self, data)
        order, product, factory = data.get("work_order"), data.get("product"), data.get("factory")
        if order and product and order.product_id != product.pk: self.add_error("product", "Product must match the selected work order.")
        if order and factory and order.factory_id != factory.pk: self.add_error("work_order", "Work order must belong to the selected factory.")
        actual, rejected = data.get("actual_quantity"), data.get("rejected_quantity")
        if actual is not None and rejected is not None and rejected > actual: self.add_error("rejected_quantity", "Rejected quantity cannot exceed actual production.")
        return data


def validate_hierarchy(form, data):
    factory, department, unit = data.get("factory"), data.get("department"), data.get("production_unit")
    if factory and department and department.factory_id != factory.pk: form.add_error("department", "Department must belong to the selected factory.")
    if department and unit and unit.department_id != department.pk: form.add_error("production_unit", "Production unit must belong to the selected department.")
    if factory and unit and unit.department.factory_id != factory.pk: form.add_error("production_unit", "Production unit must belong to the selected factory.")
