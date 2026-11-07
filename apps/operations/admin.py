from django.contrib import admin
from .models import BillingDocument, BillingLine, BillingPayment, BillingRefund, CreditDebitNote, CreditDebitNoteLine, Customer, GSTTaxProfile, GSTTaxRate, InventoryBatch, InventoryItem, ProcurementDocument, Product, ProductCategory, ProductPriceHistory, ProductionEntry, ProductionPlan, StockMovement, StockTransfer, Supplier, SupplierEvaluation, Warehouse, WarehouseBin, WorkOrder


@admin.register(SupplierEvaluation)
class SupplierEvaluationAdmin(admin.ModelAdmin):
    list_display = ("supplier", "evaluated_on", "quality_score", "delivery_score", "communication_score", "evaluated_by")
    list_filter = ("evaluated_on", "supplier__factory")
    search_fields = ("supplier__name", "notes")


@admin.register(WarehouseBin)
class WarehouseBinAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "code", "description", "is_active")
    list_filter = ("warehouse__factory", "is_active")
    search_fields = ("warehouse__name", "code")


@admin.register(InventoryBatch)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = ("item", "warehouse", "lot_number", "expiry_date", "received_at")
    list_filter = ("warehouse__factory", "expiry_date")
    search_fields = ("item__sku", "item__name", "lot_number")


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("item", "from_warehouse", "to_warehouse", "quantity", "transferred_at", "transferred_by")
    list_filter = ("from_warehouse__factory", "transferred_at")
    search_fields = ("item__sku", "reference")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "name", "customer_type", "company", "state", "email", "phone", "tax_registration_number", "credit_limit", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("customer_id", "name", "email", "tax_registration_number")


class BillingLineInline(admin.TabularInline):
    model = BillingLine
    extra = 0


class CreditDebitNoteLineInline(admin.TabularInline):
    model = CreditDebitNoteLine
    extra = 0
    readonly_fields = (
        "source_line",
        "description",
        "quantity",
        "unit_price",
        "line_total",
        "discount_amount",
        "taxable_value",
        "tax_rate",
        "hsn_sac_code",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditDebitNote)
class CreditDebitNoteAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "source_document", "factory", "issue_date", "total", "status", "approved_by")
    list_filter = ("kind", "status", "factory", "issue_date")
    search_fields = ("number", "source_document__number", "reason")
    readonly_fields = (
        "factory",
        "source_document",
        "kind",
        "number",
        "status",
        "issue_date",
        "reason",
        "subtotal",
        "discount_amount",
        "taxable_value",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "tax_amount",
        "total",
        "currency_code",
        "created_by",
        "approved_by",
        "approved_at",
        "created_at",
    )
    inlines = (CreditDebitNoteLineInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingDocument)
class BillingDocumentAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "factory", "customer", "supplier", "total", "amount_paid", "status", "due_date")
    list_filter = ("kind", "status", "factory")
    search_fields = ("number", "customer__name", "supplier__name")
    inlines = (BillingLineInline,)


@admin.register(BillingPayment)
class BillingPaymentAdmin(admin.ModelAdmin):
    list_display = ("document", "payment_date", "amount", "method", "reference", "recorded_by")
    list_filter = ("payment_date", "method")
    search_fields = ("document__number", "reference")


@admin.register(BillingRefund)
class BillingRefundAdmin(admin.ModelAdmin):
    list_display = ("payment", "refund_date", "amount", "method", "reference", "reason", "recorded_by")
    list_filter = ("refund_date", "method")
    search_fields = ("payment__document__number", "reference", "reason")


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "tracking_code", "name", "factory", "kind", "minimum_stock", "standard_cost", "is_active")
    list_filter = ("kind", "factory", "is_active")
    search_fields = ("sku", "tracking_code", "name")


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "factory", "location", "is_active")
    list_filter = ("factory", "is_active")
    search_fields = ("code", "name")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "factory", "contact_name", "email", "phone", "is_active")
    list_filter = ("factory", "is_active")
    search_fields = ("name", "contact_name", "email")


@admin.register(ProcurementDocument)
class ProcurementDocumentAdmin(admin.ModelAdmin):
    list_display = ("number", "factory", "kind", "item", "supplier", "quantity", "status", "expected_date")
    list_filter = ("kind", "status", "factory")
    search_fields = ("number", "item__sku", "supplier__name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "item", "warehouse", "quantity", "unit_cost", "reference", "created_by")
    list_filter = ("kind", "warehouse__factory")
    search_fields = ("item__sku", "reference", "procurement_document__number")


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "unit_of_measure", "selling_price", "tax_category", "tax_rate", "gst_tax_profile", "hsn_sac_code", "discount_percentage", "is_active")
    list_filter = ("category", "is_active", "tax_category")
    search_fields = ("sku", "name", "tax_category")


class GSTTaxRateInline(admin.TabularInline):
    model = GSTTaxRate
    extra = 1


@admin.register(GSTTaxProfile)
class GSTTaxProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    inlines = (GSTTaxRateInline,)


@admin.register(ProductPriceHistory)
class ProductPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("product", "old_price", "new_price", "effective_from", "notes")
    search_fields = ("product__sku", "product__name")


@admin.register(ProductionPlan)
class ProductionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "factory", "production_unit", "product", "target_quantity", "start_date", "end_date", "status")
    list_filter = ("status", "factory", "start_date")
    search_fields = ("name", "product__sku")


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "factory", "production_unit", "product", "planned_quantity", "scheduled_start", "status")
    list_filter = ("status", "factory", "scheduled_start")
    search_fields = ("order_number", "product__sku")


@admin.register(ProductionEntry)
class ProductionEntryAdmin(admin.ModelAdmin):
    list_display = ("production_date", "factory", "production_unit", "product", "actual_quantity", "rejected_quantity", "entered_by")
    list_filter = ("production_date", "factory", "department", "production_unit")
    search_fields = ("product__sku", "work_order__order_number")
