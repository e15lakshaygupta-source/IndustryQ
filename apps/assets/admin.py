from django.contrib import admin
from .models import Asset, MaintenanceRecord


class MaintenanceRecordInline(admin.TabularInline):
    model = MaintenanceRecord
    extra = 0


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_code", "name", "category",
        "department", "status", "purchase_cost"
    )
    list_filter = ("category", "status", "department")
    search_fields = ("asset_code", "name", "serial_number")
    readonly_fields = ("created_at", "updated_at")
    inlines = [MaintenanceRecordInline]


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "asset", "maintenance_type",
        "maintenance_date", "cost", "next_service_date"
    )
    list_filter = ("maintenance_type", "maintenance_date")
    search_fields = ("asset__asset_code", "asset__name")
