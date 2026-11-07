from django.contrib import admin
from .models import Applicant, Employee, PayrollRecord, Shift, Attendance, LeaveRequest, EmployeeShift, PerformanceRecord, TrainingCourse, TrainingEnrollment


@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("name", "position", "factory", "department", "status", "applied_at")
    list_filter = ("status", "factory", "department")
    search_fields = ("name", "email", "position")


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "period_start", "period_end", "gross_pay", "deductions", "status")
    list_filter = ("status", "period_end", "employee__factory")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")


@admin.register(TrainingCourse)
class TrainingCourseAdmin(admin.ModelAdmin):
    list_display = ("title", "factory", "created_at")
    list_filter = ("factory",)
    search_fields = ("title",)


@admin.register(TrainingEnrollment)
class TrainingEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("course", "employee", "status", "assigned_at", "completed_at")
    list_filter = ("status", "course__factory")
    search_fields = ("course__title", "employee__employee_id", "employee__first_name")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id", "first_name", "last_name",
        "factory", "department", "designation",
        "employment_status"
    )
    list_filter = ("factory", "department", "employment_status")
    search_fields = (
        "employee_id", "first_name", "last_name", "email"
    )
    list_editable = ("employment_status",)
    autocomplete_fields = ("reporting_manager",)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "name", "shift_code", "factory",
        "start_time", "end_time", "is_active"
    )
    list_filter = ("factory", "is_active")
    search_fields = ("name", "shift_code")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "date", "shift",
        "status", "check_in", "check_out"
    )
    list_filter = ("date", "status", "shift__factory")
    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name"
    )
    date_hierarchy = "date"


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "leave_type", "start_date",
        "end_date", "status", "reviewed_by"
    )
    list_filter = ("status", "leave_type", "start_date")
    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name"
    )
    readonly_fields = ("created_at", "reviewed_at")


@admin.register(EmployeeShift)
class EmployeeShiftAdmin(admin.ModelAdmin):
    list_display = ("employee", "shift", "start_date", "end_date", "assigned_by")
    list_filter = ("shift__factory", "start_date")
    search_fields = ("employee__employee_id", "employee__first_name", "shift__name")


@admin.register(PerformanceRecord)
class PerformanceRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "period_start", "period_end", "score", "goals_met", "recorded_by")
    list_filter = ("period_start", "period_end")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")
