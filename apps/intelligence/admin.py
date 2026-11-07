from django.contrib import admin
from .models import Machine, MaintenanceRecord, QualityInspection, SafetyIncident, SustainabilityReading, WorkflowAlert, WorkflowRule


@admin.register(WorkflowRule)
class WorkflowRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "factory", "is_enabled", "last_run_at")
    list_filter = ("event", "is_enabled", "factory")
    search_fields = ("name",)


@admin.register(WorkflowAlert)
class WorkflowAlertAdmin(admin.ModelAdmin):
    list_display = ("rule", "fingerprint", "created_at")
    list_filter = ("rule__event", "created_at")
    search_fields = ("message", "fingerprint")
    readonly_fields = ("rule", "fingerprint", "message", "target_url", "created_at")


@admin.register(QualityInspection)
class QualityInspectionAdmin(admin.ModelAdmin):
    list_display = ("product_reference", "factory", "status", "image_quality_flag", "image_brightness", "image_edge_detail", "created_at")
    list_filter = ("status", "image_quality_flag", "factory")
    search_fields = ("product_reference", "defect_label")

@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "name", "factory", "department", "production_unit", "purchase_cost", "warranty_expiry", "replacement_date", "status")
    list_filter = ("status", "factory", "department")
    search_fields = ("asset_tag", "name", "serial_number")


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = ("machine", "kind", "summary", "scheduled_for", "status", "reported_by")
    list_filter = ("kind", "status")
    search_fields = ("machine__asset_tag", "summary", "details")


@admin.register(SafetyIncident)
class SafetyIncidentAdmin(admin.ModelAdmin):
    list_display = ("summary", "factory", "department", "incident_date", "severity", "status", "reported_by")
    list_filter = ("severity", "status", "factory")
    search_fields = ("summary", "details")


@admin.register(SustainabilityReading)
class SustainabilityReadingAdmin(admin.ModelAdmin):
    list_display = ("reading_date", "resource", "factory", "department", "quantity", "unit_cost", "currency_code", "recorded_by")
    list_filter = ("resource", "factory", "reading_date")
    search_fields = ("reference", "notes", "factory__name")
