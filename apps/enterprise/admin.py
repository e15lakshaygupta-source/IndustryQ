from django.contrib import admin
from .models import ComplianceItem, DocumentVersion, IndustrialProject, IntegrationApp, ManagedDocument, PortalAccess, ProjectTask, RecoveryTest

class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0

@admin.register(ManagedDocument)
class ManagedDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "company", "factory", "updated_at")
    list_filter = ("category", "company", "factory")
    search_fields = ("title",)
    inlines = (DocumentVersionInline,)

@admin.register(ComplianceItem)
class ComplianceItemAdmin(admin.ModelAdmin):
    list_display = ("title", "factory", "due_date", "risk_level", "status", "owner")
    list_filter = ("factory", "risk_level", "status", "category")
    search_fields = ("title", "requirement", "notes")

class ProjectTaskInline(admin.TabularInline):
    model = ProjectTask
    extra = 0

@admin.register(IndustrialProject)
class IndustrialProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "factory", "status", "manager", "due_date", "budget")
    list_filter = ("factory", "status")
    search_fields = ("name", "description")
    inlines = (ProjectTaskInline,)

@admin.register(PortalAccess)
class PortalAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "party_type", "customer", "supplier", "is_active")
    list_filter = ("party_type", "is_active")

@admin.register(IntegrationApp)
class IntegrationAppAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "factory", "token_prefix", "is_active", "last_used_at")
    list_filter = ("provider", "factory", "is_active")

@admin.register(RecoveryTest)
class RecoveryTestAdmin(admin.ModelAdmin):
    list_display = ("archive_name", "passed", "tested_by", "tested_at")
    list_filter = ("passed", "tested_at")
