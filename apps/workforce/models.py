from django.conf import settings
from django.db import models


class Employee(models.Model):
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ON_LEAVE = "on_leave", "On Leave"
        RESIGNED = "resigned", "Resigned"

    employee_id = models.CharField(max_length=30, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_record")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    factory = models.ForeignKey("organization.Factory", on_delete=models.PROTECT, related_name="employees")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="employees")
    designation = models.CharField(max_length=120)
    joining_date = models.DateField()
    reporting_manager = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="team_members")
    employment_status = models.CharField(max_length=20, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.employee_id} - {self.first_name} {self.last_name}".strip()


class Shift(models.Model):
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="shifts")
    name = models.CharField(max_length=100)
    shift_code = models.CharField(max_length=30)
    start_time = models.TimeField()
    end_time = models.TimeField()
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["factory", "shift_code"], name="unique_shift_code_per_factory")]

    def __str__(self):
        return f"{self.name} - {self.factory.name}"


class EmployeeShift(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="shift_assignments")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="employee_assignments")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")), name="valid_employee_shift_dates")]

    def __str__(self):
        return f"{self.employee} ? {self.shift}"


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LATE = "late", "Late"
        HALF_DAY = "half_day", "Half Day"
        ON_LEAVE = "on_leave", "On Leave"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_records")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    remarks = models.CharField(max_length=250, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "employee__employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="unique_employee_attendance_per_day")]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.date} - {self.get_status_display()}"


class LeaveRequest(models.Model):
    class LeaveType(models.TextChoices):
        CASUAL = "casual", "Casual Leave"
        SICK = "sick", "Sick Leave"
        EARNED = "earned", "Earned Leave"
        UNPAID = "unpaid", "Unpaid Leave"
        OTHER = "other", "Other"

    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_leave_requests")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.get_leave_type_display()} ({self.status})"


class PerformanceRecord(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="performance_records")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="employee_performance_records")
    period_start = models.DateField()
    period_end = models.DateField()
    score = models.DecimalField(max_digits=5, decimal_places=2)
    goals_met = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.period_start} to {self.period_end}: {self.score}"


class Applicant(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        SCREENING = "screening", "Screening"
        INTERVIEW = "interview", "Interview"
        OFFER = "offer", "Offer"
        HIRED = "hired", "Hired"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    position = models.CharField(max_length=120)
    factory = models.ForeignKey("organization.Factory", on_delete=models.PROTECT, related_name="applicants")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="applicants")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    applied_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"{self.name} - {self.position}"


class PayrollRecord(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PAID = "paid", "Paid"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="payroll_records")
    period_start = models.DateField()
    period_end = models.DateField()
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "employee__employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "period_start", "period_end"], name="unique_payroll_employee_period")]

    @property
    def net_pay(self):
        return self.gross_pay - self.deductions


class TrainingCourse(models.Model):
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    factory = models.ForeignKey("organization.Factory", on_delete=models.CASCADE, related_name="training_courses")
    employees = models.ManyToManyField(Employee, through="TrainingEnrollment", related_name="training_courses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class TrainingEnrollment(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        COMPLETE = "complete", "Complete"

    course = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE, related_name="enrollments")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="training_enrollments")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ASSIGNED)
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["course", "employee"], name="unique_training_enrollment")]
