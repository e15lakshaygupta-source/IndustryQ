from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.organization.models import Department, Factory
from .models import Applicant, Attendance, Employee, EmployeeShift, LeaveRequest, PayrollRecord, PerformanceRecord, Shift, TrainingCourse, TrainingEnrollment

User = get_user_model()


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ("employee_id", "user", "first_name", "last_name", "email", "phone", "factory", "department", "designation", "joining_date", "reporting_manager", "employment_status")
        widgets = {"joining_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, allowed_factories=None, allowed_departments=None, allowed_managers=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments
        if allowed_managers is not None: self.fields["reporting_manager"].queryset = allowed_managers
        self.fields["user"].queryset = User.objects.filter(employee_record__isnull=True) | (User.objects.filter(pk=self.instance.user_id) if self.instance.pk and self.instance.user_id else User.objects.none())
        self.fields["user"].required = False

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if factory and department and department.factory_id != factory.pk: self.add_error("department", "Select a department belonging to the chosen factory.")
        return cleaned


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ("factory", "name", "shift_code", "start_time", "end_time", "description", "is_active")
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}), "end_time": forms.TimeInput(attrs={"type": "time"})}
    def __init__(self, *args, allowed_factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ("employee", "date", "shift", "status", "check_in", "check_out", "remarks")
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "check_in": forms.TimeInput(attrs={"type": "time"}), "check_out": forms.TimeInput(attrs={"type": "time"})}
    def __init__(self, *args, employees=None, shifts=None, **kwargs):
        super().__init__(*args, **kwargs)
        if employees is not None: self.fields["employee"].queryset = employees
        if shifts is not None: self.fields["shift"].queryset = shifts
    def clean(self):
        cleaned = super().clean()
        employee, shift = cleaned.get("employee"), cleaned.get("shift")
        if employee and shift and shift.factory_id != employee.factory_id: self.add_error("shift", "The shift must belong to the employee's factory.")
        if cleaned.get("check_in") and cleaned.get("check_out") and cleaned["check_out"] < cleaned["check_in"]: self.add_error("check_out", "Check-out must be after check-in.")
        return cleaned


class LeaveApplicationForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ("leave_type", "start_date", "end_date", "reason")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"}), "reason": forms.Textarea(attrs={"rows": 3})}
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start: self.add_error("end_date", "End date must be on or after the start date.")
        if start and start < timezone.localdate(): self.add_error("start_date", "Leave applications cannot start in the past.")
        return cleaned


class PerformanceRecordForm(forms.ModelForm):
    class Meta:
        model = PerformanceRecord
        fields = ("period_start", "period_end", "score", "goals_met", "notes")
        widgets = {"period_start": forms.DateInput(attrs={"type": "date"}), "period_end": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}
    def clean(self):
        cleaned = super().clean()
        start, end, score = cleaned.get("period_start"), cleaned.get("period_end"), cleaned.get("score")
        if start and end and end < start: self.add_error("period_end", "Period end must be on or after the start date.")
        if score is not None and not 0 <= score <= 100: self.add_error("score", "Score must be between 0 and 100.")
        return cleaned


class ApplicantForm(forms.ModelForm):
    class Meta:
        model = Applicant
        fields = ("name", "email", "phone", "position", "factory", "department", "status", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, allowed_factories=None, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
        if allowed_departments is not None: self.fields["department"].queryset = allowed_departments
    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if factory and department and department.factory_id != factory.pk: self.add_error("department", "Select a department belonging to the chosen factory.")
        return cleaned


class PayrollRecordForm(forms.ModelForm):
    class Meta:
        model = PayrollRecord
        fields = ("employee", "period_start", "period_end", "gross_pay", "deductions", "status")
        widgets = {"period_start": forms.DateInput(attrs={"type": "date"}), "period_end": forms.DateInput(attrs={"type": "date"})}
    def __init__(self, *args, employees=None, **kwargs):
        super().__init__(*args, **kwargs)
        if employees is not None: self.fields["employee"].queryset = employees
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end and end < start: self.add_error("period_end", "Period end must be on or after the start date.")
        gross, deductions = cleaned.get("gross_pay"), cleaned.get("deductions")
        if gross is not None and deductions is not None and deductions > gross: self.add_error("deductions", "Deductions cannot exceed gross pay.")
        return cleaned


class TrainingCourseForm(forms.ModelForm):
    class Meta:
        model = TrainingCourse
        fields = ("title", "description", "factory")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, allowed_factories=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_factories is not None: self.fields["factory"].queryset = allowed_factories
