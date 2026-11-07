from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "factory_name",
        "department_name",
        "company",
        "phone",
        "job_title",
        "created_at",
    )

    list_filter = ("role", "company", "factory", "department")
    search_fields = ("user__username", "user__email")
    list_editable = ("role",)
