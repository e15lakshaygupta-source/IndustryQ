from django.contrib import admin
from .models import SavedKPIDashboard


@admin.register(SavedKPIDashboard)
class SavedKPIDashboardAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "factory", "created_at")
    list_filter = ("factory", "created_at")
    search_fields = ("name", "user__username")
