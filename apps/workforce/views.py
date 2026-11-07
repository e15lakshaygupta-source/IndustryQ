from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Count, Avg
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import UserActivity, UserProfile
from apps.organization.models import Company, Factory, Department
from .forms import ApplicantForm, AttendanceForm, EmployeeForm, LeaveApplicationForm, PayrollRecordForm, PerformanceRecordForm, ShiftForm, TrainingCourseForm
from .models import Applicant, Attendance, Employee, EmployeeShift, LeaveRequest, PayrollRecord, PerformanceRecord, Shift, TrainingCourse, TrainingEnrollment

MANAGER_ROLES = (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER)


def scope(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE})
    if user.is_superuser and profile.role != UserProfile.Role.SUPER_ADMIN:
        profile.role = UserProfile.Role.SUPER_ADMIN
        profile.save(update_fields=["role", "updated_at"])
    companies, factories, departments = Company.objects.all(), Factory.objects.all(), Department.objects.all()
    employees = Employee.objects.select_related("factory", "department", "user")
    shifts = Shift.objects.select_related("factory")
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
        departments = departments.filter(factory__in=factories)
        employees = employees.filter(factory__in=factories)
        shifts = shifts.filter(factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        departments = departments.filter(factory_id=profile.factory_id)
        employees = employees.filter(factory_id=profile.factory_id)
        shifts = shifts.filter(factory_id=profile.factory_id)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
            departments = departments.filter(pk=profile.department_id)
            employees = employees.filter(department_id=profile.department_id)
    if profile.role == UserProfile.Role.EMPLOYEE:
        employees = employees.filter(user=user)
    return profile, companies, factories, departments, employees, shifts


def manager_required(profile):
    return profile.role in MANAGER_ROLES


def page(request, template, context):
    return render(request, template, context)


@login_required
def workforce_home(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    attendance = Attendance.objects.filter(employee__in=employees)
    leaves = LeaveRequest.objects.filter(employee__in=employees)
    today = timezone.localdate()
    context = {"profile": profile, "employee_count": employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count(), "department_count": departments.count(), "factory_count": factories.count(), "present_today": attendance.filter(date=today, status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY]).count(), "pending_leaves": leaves.filter(status=LeaveRequest.ApprovalStatus.PENDING).count(), "employees": employees[:8], "my_employee": employees.filter(user=request.user).first(), "can_manage": manager_required(profile)}
    return page(request, "workforce/home.html", context)


@login_required
def employee_directory(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    query = request.GET.get("q", "").strip()
    if query: employees = employees.filter(Q(employee_id__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query) | Q(designation__icontains=query))
    status = request.GET.get("status", "")
    if status in Employee.EmploymentStatus.values: employees = employees.filter(employment_status=status)
    return page(request, "workforce/employee_list.html", {"page_obj": Paginator(employees, 20).get_page(request.GET.get("page")), "query": query, "status": status, "can_manage": manager_required(profile)})


@login_required
def employee_detail(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    employee = get_object_or_404(employees, pk=pk)
    if request.method == "POST" and request.POST.get("action") == "apply_leave":
        if employee.user_id != request.user.id: raise Http404
        form = LeaveApplicationForm(request.POST)
        if form.is_valid():
            leave = form.save(commit=False); leave.employee = employee; leave.save()
            UserActivity.objects.create(user=request.user, action="leave_applied", details={"leave_id": leave.pk}, ip_address=request.META.get("REMOTE_ADDR"))
            messages.success(request, "Leave application submitted for approval.")
            return redirect("workforce:employee_detail", pk=employee.pk)
    else: form = LeaveApplicationForm()
    return page(request, "workforce/employee_detail.html", {"employee": employee, "attendance": employee.attendance_records.select_related("shift")[:20], "leaves": employee.leave_requests.all()[:20], "performance": employee.performance_records.all()[:20], "assignments": employee.shift_assignments.select_related("shift")[:10], "leave_form": form, "can_manage": manager_required(profile), "is_self": employee.user_id == request.user.id})


@login_required
def employee_create(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    initial = request.session.pop("onboarding_prefill", None)
    if initial:
        parts = initial.pop("first_name", "").split(" ", 1)
        initial["first_name"] = parts[0] if parts else ""
        initial["last_name"] = parts[1] if len(parts) > 1 else ""
    form = EmployeeForm(request.POST or None, initial=initial, allowed_factories=factories, allowed_departments=departments, allowed_managers=employees)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        UserActivity.objects.create(user=request.user, action="employee_registered", details={"employee_id": employee.employee_id}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Employee registered.")
        return redirect("workforce:employee_detail", pk=employee.pk)
    return page(request, "workforce/form.html", {"form": form, "title": "Register employee", "button": "Save employee"})


@login_required
def employee_edit(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    employee = get_object_or_404(employees, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee, allowed_factories=factories, allowed_departments=departments, allowed_managers=employees)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        UserActivity.objects.create(user=request.user, action="employee_updated", details={"employee_id": employee.employee_id}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Employee profile updated.")
        return redirect("workforce:employee_detail", pk=employee.pk)
    return page(request, "workforce/form.html", {"form": form, "title": "Edit employee", "button": "Save changes"})


@login_required
def shift_list(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    return page(request, "workforce/shift_list.html", {"shifts": shifts, "can_manage": manager_required(profile)})


@login_required
def shift_create(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    form = ShiftForm(request.POST or None, allowed_factories=factories)
    if request.method == "POST" and form.is_valid():
        shift = form.save()
        UserActivity.objects.create(user=request.user, action="shift_created", details={"shift_id": shift.pk}, ip_address=request.META.get("REMOTE_ADDR"))
        return redirect("workforce:shift_list")
    return page(request, "workforce/form.html", {"form": form, "title": "Create shift", "button": "Save shift"})


@login_required
def shift_assign(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    shift = get_object_or_404(shifts, pk=pk)
    if request.method == "POST":
        employee = get_object_or_404(employees, pk=request.POST.get("employee"))
        if employee.factory_id != shift.factory_id:
            messages.error(request, "The employee and shift must belong to the same factory.")
        else:
            EmployeeShift.objects.filter(employee=employee, end_date__isnull=True).update(end_date=timezone.localdate())
            EmployeeShift.objects.create(employee=employee, shift=shift, start_date=timezone.localdate(), assigned_by=request.user)
            UserActivity.objects.create(user=request.user, action="shift_assigned", details={"employee_id": employee.employee_id, "shift_id": shift.pk})
            messages.success(request, "Shift assigned.")
            return redirect("workforce:shift_list")
    return page(request, "workforce/shift_assign.html", {"shift": shift, "employees": employees.filter(factory=shift.factory, employment_status=Employee.EmploymentStatus.ACTIVE)})


@login_required
def attendance_list(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    records = Attendance.objects.filter(employee__in=employees).select_related("employee", "employee__factory", "employee__department", "shift")
    selected_date = request.GET.get("date", timezone.localdate().isoformat())
    try: date.fromisoformat(selected_date)
    except ValueError: selected_date = timezone.localdate().isoformat()
    records = records.filter(date=selected_date)
    return page(request, "workforce/attendance_list.html", {"records": records, "date": selected_date, "can_manage": manager_required(profile)})


@login_required
def attendance_create(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    form = AttendanceForm(request.POST or None, employees=employees, shifts=shifts)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        UserActivity.objects.create(user=request.user, action="attendance_recorded", details={"employee_id": record.employee.employee_id, "date": str(record.date)}, ip_address=request.META.get("REMOTE_ADDR"))
        return redirect("workforce:attendance_list")
    return page(request, "workforce/form.html", {"form": form, "title": "Record attendance", "button": "Save attendance"})


@login_required
def attendance_punch(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    employee = employees.filter(user=request.user).first()
    if not employee: raise Http404
    if request.method == "POST":
        today, now = timezone.localdate(), timezone.localtime().time().replace(second=0, microsecond=0)
        action_requested = request.POST.get("action")
        record = Attendance.objects.filter(employee=employee, date=today).first()
        if action_requested == "check_out" and record is None:
            record = Attendance.objects.filter(employee=employee, date=today - timedelta(days=1), check_in__isnull=False, check_out__isnull=True).first()
        if record is None:
            shift_id = employee.shift_assignments.filter(start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).values_list("shift_id", flat=True).first()
            record = Attendance.objects.create(employee=employee, date=today, status=Attendance.Status.PRESENT, shift_id=shift_id)
        if action_requested == "check_in" and not record.check_in:
            record.check_in, record.status = now, Attendance.Status.PRESENT
            record.save(update_fields=["check_in", "status"]); action = "checked_in"
        elif request.POST.get("action") == "check_out" and record.check_in and not record.check_out:
            record.check_out = now; record.save(update_fields=["check_out"]); action = "checked_out"
        else:
            messages.info(request, "That attendance action is not available right now.")
            return redirect("workforce:portal")
        UserActivity.objects.create(user=request.user, action=action, details={"employee_id": employee.employee_id}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Attendance updated.")
        return redirect("workforce:portal")
    return redirect("workforce:portal")


@login_required
def leave_list(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    leaves = LeaveRequest.objects.filter(employee__in=employees).select_related("employee", "reviewed_by")
    return page(request, "workforce/leave_list.html", {"leaves": leaves[:100], "can_manage": manager_required(profile)})


@login_required
def leave_review(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    leave = get_object_or_404(LeaveRequest.objects.filter(employee__in=employees), pk=pk)
    if request.method == "POST" and leave.status == LeaveRequest.ApprovalStatus.PENDING:
        choice = request.POST.get("status")
        if choice in (LeaveRequest.ApprovalStatus.APPROVED, LeaveRequest.ApprovalStatus.REJECTED):
            leave.status, leave.reviewed_by, leave.reviewed_at = choice, request.user, timezone.now()
            leave.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            if choice == LeaveRequest.ApprovalStatus.APPROVED:
                Attendance.objects.filter(employee=leave.employee, date__range=(leave.start_date, leave.end_date)).update(status=Attendance.Status.ON_LEAVE)
            UserActivity.objects.create(user=request.user, action="leave_reviewed", details={"leave_id": leave.pk, "status": choice}, ip_address=request.META.get("REMOTE_ADDR"))
            messages.success(request, "Leave decision saved.")
    return redirect("workforce:leave_list")


@login_required
def attendance_report(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    records = Attendance.objects.filter(employee__in=employees)
    start, end = request.GET.get("start", ""), request.GET.get("end", "")
    if start:
        try: date.fromisoformat(start); records = records.filter(date__gte=start)
        except ValueError: start = ""
    if end:
        try: date.fromisoformat(end); records = records.filter(date__lte=end)
        except ValueError: end = ""
    summary = records.values("employee__employee_id", "employee__first_name", "employee__last_name", "employee__factory__name", "employee__department__name").annotate(days=Count("id"), present=Count("id", filter=Q(status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY])), absent=Count("id", filter=Q(status=Attendance.Status.ABSENT)), on_leave=Count("id", filter=Q(status=Attendance.Status.ON_LEAVE))).order_by("employee__employee_id")
    if request.GET.get("format") == "csv":
        response = HttpResponse("Employee ID,Name,Factory,Department,Days,Present,Absent,On Leave\n", content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=attendance-report.csv"
        for row in summary: response.write(f"{row['employee__employee_id']},{row['employee__first_name']} {row['employee__last_name']},{row['employee__factory__name']},{row['employee__department__name']},{row['days']},{row['present']},{row['absent']},{row['on_leave']}\n")
        return response
    report_page = Paginator(summary, 30).get_page(request.GET.get("page"))
    return page(request, "workforce/attendance_report.html", {"rows": report_page, "start": start, "end": end})


@login_required
def performance_create(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    employee = get_object_or_404(employees, pk=pk)
    form = PerformanceRecordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False); record.employee = employee; record.recorded_by = request.user; record.save()
        UserActivity.objects.create(user=request.user, action="performance_recorded", details={"employee_id": employee.employee_id, "record_id": record.pk})
        messages.success(request, "Performance record saved.")
        return redirect("workforce:employee_detail", pk=employee.pk)
    return page(request, "workforce/form.html", {"form": form, "title": f"Performance record: {employee}", "button": "Save record"})


@login_required
def recruitment(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    applicants = Applicant.objects.filter(factory__in=factories).select_related("factory", "department")
    form = ApplicantForm(request.POST or None, allowed_factories=factories, allowed_departments=departments)
    if request.method == "POST" and form.is_valid():
        applicant = form.save()
        UserActivity.objects.create(user=request.user, action="applicant_created", details={"applicant_id": applicant.pk})
        messages.success(request, "Applicant added to the recruitment pipeline.")
        return redirect("workforce:recruitment")
    return page(request, "workforce/recruitment.html", {"applicants": applicants[:100], "form": form})


@login_required
def applicant_status(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    if not manager_required(profile): raise Http404
    applicant = get_object_or_404(Applicant, pk=pk, factory__in=factories)
    if request.method == "POST" and request.POST.get("status") in Applicant.Status.values:
        applicant.status = request.POST["status"]
        applicant.save(update_fields=["status"])
        if applicant.status == Applicant.Status.HIRED and not Employee.objects.filter(email__iexact=applicant.email, factory=applicant.factory).exists():
            request.session["onboarding_prefill"] = {"first_name": applicant.name, "email": applicant.email, "phone": applicant.phone, "designation": applicant.position, "factory": applicant.factory_id, "department": applicant.department_id}
            messages.success(request, "Applicant marked hired. Complete the onboarding employee record.")
            return redirect("workforce:employee_create")
        messages.success(request, "Applicant stage updated.")
    return redirect("workforce:recruitment")


@login_required
def payroll(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    own_employee = Employee.objects.filter(user=request.user).first()
    records = PayrollRecord.objects.filter(employee__in=employees).select_related("employee")
    if not manager_required(profile): records = records.filter(employee=own_employee) if own_employee else records.none()
    form = PayrollRecordForm(request.POST or None, employees=employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE)) if manager_required(profile) else None
    if request.method == "POST" and form and form.is_valid():
        record = form.save(commit=False); record.created_by = request.user; record.save()
        UserActivity.objects.create(user=request.user, action="payroll_record_created", details={"payroll_id": record.pk})
        messages.success(request, "Payroll record saved.")
        return redirect("workforce:payroll")
    return page(request, "workforce/payroll.html", {"records": records[:200], "form": form, "can_manage": manager_required(profile)})


@login_required
def training(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    courses = TrainingCourse.objects.filter(factory__in=factories).prefetch_related("enrollments__employee")
    form = TrainingCourseForm(request.POST or None, allowed_factories=factories) if manager_required(profile) else None
    if request.method == "POST" and form and form.is_valid():
        course = form.save()
        employees_to_assign = employees.filter(factory=course.factory, employment_status=Employee.EmploymentStatus.ACTIVE)
        TrainingEnrollment.objects.bulk_create([TrainingEnrollment(course=course, employee=employee) for employee in employees_to_assign], ignore_conflicts=True)
        messages.success(request, "Training course created and assigned to active employees at its factory.")
        return redirect("workforce:training")
    enrollments = TrainingEnrollment.objects.filter(course__in=courses).select_related("course", "employee")
    if not manager_required(profile): enrollments = enrollments.filter(employee__user=request.user)
    return page(request, "workforce/training.html", {"courses": courses, "enrollments": enrollments[:200], "form": form, "can_manage": manager_required(profile)})


@login_required
def training_complete(request, pk):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    enrollment = get_object_or_404(TrainingEnrollment.objects.select_related("course", "employee").filter(course__factory__in=factories), pk=pk)
    if not manager_required(profile) and enrollment.employee.user_id != request.user.id: raise Http404
    if request.method == "POST" and enrollment.status != TrainingEnrollment.Status.COMPLETE:
        enrollment.status = TrainingEnrollment.Status.COMPLETE
        enrollment.completed_at = timezone.now()
        enrollment.save(update_fields=["status", "completed_at"])
        messages.success(request, "Training completion recorded.")
    return redirect("workforce:training")


@login_required
def employee_portal(request):
    profile, companies, factories, departments, employees, shifts = scope(request.user)
    employee = Employee.objects.filter(user=request.user).first()
    if not employee:
        messages.info(request, "Your login is not linked to an employee profile yet. Ask an administrator to link it from the employee record.")
        return redirect("workforce:home")
    today = timezone.localdate()
    attendance = employee.attendance_records.filter(date=today).first()
    assignments = employee.shift_assignments.select_related("shift").filter(start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    return page(request, "workforce/portal.html", {"employee": employee, "attendance_today": attendance, "assignments": assignments, "leaves": employee.leave_requests.all()[:10], "performance": employee.performance_records.all()[:5], "payroll_records": employee.payroll_records.all()[:12], "training_enrollments": employee.training_enrollments.select_related("course").all()[:20], "now": timezone.localtime()})
