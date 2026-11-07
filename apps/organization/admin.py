from django.contrib import admin
from .models import Company, Factory, Department, ProductionUnit


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "industry_type", "city", "country", "is_active")
    list_filter = ("is_active", "industry_type", "country")
    search_fields = ("name", "registration_number", "city")


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = (
        "name", "factory_code", "company", "city",
        "state", "state_code", "gstin", "is_active"
    )
    list_filter = ("company", "state", "is_active")
    search_fields = ("name", "factory_code", "city")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = (
        "name", "department_code", "factory",
        "manager_name", "is_active"
    )
    list_filter = ("factory", "is_active")
    search_fields = ("name", "department_code", "manager_name")


@admin.register(ProductionUnit)
class ProductionUnitAdmin(admin.ModelAdmin):
    list_display = (
        "name", "unit_code", "department", "is_active"
    )
    list_filter = ("department", "is_active")
    search_fields = ("name", "unit_code")
