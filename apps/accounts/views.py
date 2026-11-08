from decimal import Decimal

from django.contrib.auth import login, logout
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.paginator import Paginator
from django.urls import reverse
from django.db import transaction
from django.db.models import Q, Sum, Case, When, F, DecimalField

from .models import UserProfile
from .models import UserActivity
from .forms import ManagedUserForm, PremiumAuthenticationForm, ProfileForm, RegistrationForm, SecurePasswordChangeForm, UserAssignmentForm
from apps.organization.models import (
    Company, Factory, Department, ProductionUnit
)
from apps.workforce.models import (
    Employee, Shift, Attendance, LeaveRequest
)
from apps.operations.models import BillingDocument, InventoryItem, ProductionEntry, ProductionPlan, StockMovement, WorkOrder
from apps.operations.views import billing_manager
from apps.enterprise.models import IndustrialProject
from apps.enterprise.models import ComplianceItem
from apps.core.views import _billing_dashboard_context
from apps.intelligence.models import Machine


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = PremiumAuthenticationForm(request, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)

        default_role = (
            UserProfile.Role.SUPER_ADMIN
            if user.is_superuser
            else UserProfile.Role.EMPLOYEE
        )

        UserProfile.objects.get_or_create(
            user=user,
            defaults={"role": default_role}
        )
        UserActivity.objects.create(user=user, action="login", ip_address=request.META.get("REMOTE_ADDR"))

        if user.is_superuser:
            UserProfile.objects.filter(user=user).update(
                role=UserProfile.Role.SUPER_ADMIN
            )

        return redirect("dashboard")

    return render(request, "accounts/login.html", {"form": form})


@login_required
def dashboard_view(request):
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE},
    )
    today = timezone.localdate()
    companies = Company.objects.all()
    factories = Factory.objects.select_related("company")
    departments = Department.objects.all()
    units = ProductionUnit.objects.all()
    employees = Employee.objects.select_related("factory", "department")
    shifts = Shift.objects.filter(is_active=True)
    attendance = Attendance.objects.all()
    leaves = LeaveRequest.objects.all()

    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
        departments = departments.filter(factory__in=factories)
        units = units.filter(department__factory__in=factories)
        employees = employees.filter(factory__in=factories)
        shifts = shifts.filter(factory__in=factories)
        attendance = attendance.filter(employee__factory__in=factories)
        leaves = leaves.filter(employee__factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        departments = departments.filter(factory_id=profile.factory_id)
        units = units.filter(department__factory_id=profile.factory_id)
        employees = employees.filter(factory_id=profile.factory_id)
        shifts = shifts.filter(factory_id=profile.factory_id)
        attendance = attendance.filter(employee__factory_id=profile.factory_id)
        leaves = leaves.filter(employee__factory_id=profile.factory_id)

    production_entries = ProductionEntry.objects.filter(factory__in=factories)
    production_plans = ProductionPlan.objects.filter(factory__in=factories)
    work_orders = WorkOrder.objects.filter(factory__in=factories)
    if profile.role == UserProfile.Role.DEPARTMENT_MANAGER:
        production_entries = production_entries.filter(department_id=profile.department_id)
        production_plans = production_plans.filter(department_id=profile.department_id)
        work_orders = work_orders.filter(department_id=profile.department_id)

    production_today = production_entries.filter(production_date=today)
    inventory_items = InventoryItem.objects.filter(factory__in=factories, is_active=True)
    inventory_value, inventory_low_stock = Decimal("0"), 0
    for item in inventory_items:
        stock = StockMovement.objects.filter(item=item).aggregate(total=Sum(Case(
            When(kind__in=[StockMovement.Kind.RECEIPT, StockMovement.Kind.ADJUST_IN], then=F("quantity")),
            default=F("quantity") * -1,
            output_field=DecimalField(max_digits=16, decimal_places=3),
        )))['total'] or 0
        inventory_value += stock * item.standard_cost
        if item.minimum_stock > 0 and stock <= item.minimum_stock:
            inventory_low_stock += 1

    sales_invoices = BillingDocument.objects.filter(factory__in=factories)
    billing_access = billing_manager(request.user, profile)
    outstanding_invoices = sales_invoices.filter(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])
    billing_totals = outstanding_invoices.aggregate(total=Sum("total"), paid=Sum("amount_paid"))
    machines = Machine.objects.filter(factory__in=factories)
    projects = IndustrialProject.objects.filter(factory__in=factories)
    context = {
        "profile": profile,
        "today": today,
        "asset_count": machines.count(),
        "ongoing_operations": work_orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(),
        "project_count": projects.exclude(status=IndustrialProject.Status.COMPLETE).count(),
        "production_actual_today": production_today.aggregate(total=Sum("actual_quantity"))["total"] or 0,
        "production_rejected_today": production_today.aggregate(total=Sum("rejected_quantity"))["total"] or 0,
        "production_planned_today": work_orders.filter(scheduled_start__date=today).aggregate(total=Sum("planned_quantity"))["total"] or 0,
        "open_work_orders": work_orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(),
        "active_production_plans": production_plans.exclude(status__in=[ProductionPlan.Status.COMPLETED, ProductionPlan.Status.CANCELLED]).count(),
        "inventory_item_count": inventory_items.count(),
        "inventory_low_stock": inventory_low_stock,
        "inventory_value": inventory_value,
        "billing_access": billing_access,
        "billing_draft_count": sales_invoices.filter(status=BillingDocument.Status.DRAFT).count(),
        "billing_open_count": outstanding_invoices.count(),
        "billing_overdue_count": outstanding_invoices.filter(due_date__lt=today).count(),
        "billing_balance_due": (billing_totals["total"] or Decimal("0.00")) - (billing_totals["paid"] or Decimal("0.00")),
        "total_companies": companies.count(),
        "total_factories": factories.count(),
        "total_departments": departments.count(),
        "total_units": units.count(),
        "total_employees": employees.count(),
        "active_employees": employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count(),
        "total_shifts": shifts.count(),
        "present_today": attendance.filter(date=today, status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY]).count(),
        "absent_today": attendance.filter(date=today, status=Attendance.Status.ABSENT).count(),
        "pending_leaves": leaves.filter(status=LeaveRequest.ApprovalStatus.PENDING).count(),
        "companies": companies.order_by("name")[:5],
        "factories": factories.order_by("name")[:10],
        "recent_employees": employees.order_by("-created_at")[:8],
        "recent_activities": UserActivity.objects.select_related("user").order_by("-created_at")[:8],
    }
    context.update(_billing_dashboard_context(request, profile, factories))
    return render(request, "accounts/dashboard.html", context)


@login_required
def global_search_view(request):
    """Search operational records while applying the same organization scope as dashboards."""
    query = request.GET.get("q", "").strip()[:100]
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE},
    )
    factories = Factory.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        factories = factories.filter(company_id=profile.company_id) if profile.company_id else factories.none()
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id) if profile.factory_id else factories.none()

    employees = Employee.objects.filter(factory__in=factories).select_related("factory", "department")
    machines = Machine.objects.filter(factory__in=factories).select_related("factory", "department")
    inventory = InventoryItem.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    invoices = BillingDocument.objects.filter(factory__in=factories).select_related("factory", "customer")
    if profile.role == UserProfile.Role.DEPARTMENT_MANAGER:
        employees = employees.filter(department_id=profile.department_id)
        machines = machines.filter(department_id=profile.department_id)
    if profile.role == UserProfile.Role.EMPLOYEE:
        employees = employees.filter(user=request.user)
        invoices = invoices.none()

    results = []
    if query:
        for employee in employees.filter(
            Q(employee_id__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(email__icontains=query)
        )[:20]:
            results.append({"kind": "Employee", "title": f"{employee.first_name} {employee.last_name}".strip(), "detail": f"{employee.employee_id} · {employee.factory.name}", "url": reverse("workforce:employee_detail", args=[employee.pk])})
        for machine in machines.filter(Q(asset_tag__icontains=query) | Q(name__icontains=query) | Q(serial_number__icontains=query))[:20]:
            results.append({"kind": "Machine", "title": machine.name, "detail": f"{machine.asset_tag} · {machine.factory.name}", "url": reverse("intelligence:machines")})
        for item in inventory.filter(Q(sku__icontains=query) | Q(tracking_code__icontains=query) | Q(name__icontains=query))[:20]:
            results.append({"kind": "Inventory", "title": item.name, "detail": f"{item.sku} · {item.factory.name}", "url": reverse("operations:inventory")})
        if profile.role != UserProfile.Role.EMPLOYEE and billing_manager(request.user, profile):
            for invoice in invoices.filter(Q(number__icontains=query) | Q(supplier_invoice_number__icontains=query) | Q(customer__name__icontains=query))[:20]:
                results.append({"kind": "Invoice", "title": invoice.number or invoice.supplier_invoice_number, "detail": f"{invoice.get_status_display()} · {invoice.factory.name}", "url": reverse("billing:document_detail", args=[invoice.pk])})
    return render(request, "accounts/global_search.html", {"query": query, "results": results})


@login_required
@require_POST
def logout_view(request):
    UserActivity.objects.create(user=request.user, action="logout", ip_address=request.META.get("REMOTE_ADDR"))
    logout(request)
    return redirect("login")


def register_view(request):
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        UserActivity.objects.create(user=user, action="self_registration", ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Your account is ready. Please sign in.")
        return redirect("login")
    return render(request, "accounts/form_page.html", {"form": form, "title": "Create your account", "button": "Register"})


@login_required
def profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    changing_password = request.method == "POST" and (
        request.POST.get("action") == "change_password"
        or request.resolver_match.url_name == "password_change"
    )
    form = ProfileForm(
        request.POST if request.method == "POST" and not changing_password else None,
        instance=profile,
        user=request.user,
    )
    password_form = SecurePasswordChangeForm(
        request.user,
        request.POST if changing_password else None,
    )
    if changing_password and password_form.is_valid():
        password_form.save()
        update_session_auth_hash(request, request.user)
        UserActivity.objects.create(user=request.user, action="password_changed", ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Password changed successfully.")
        return redirect("/profile/#security")
    if request.method == "POST" and not changing_password and form.is_valid():
        form.save()
        UserActivity.objects.create(user=request.user, action="profile_updated", ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Profile updated.")
        return redirect("profile")
    complete_fields = [request.user.first_name, request.user.last_name, request.user.email, profile.phone, profile.job_title, profile.address, profile.emergency_contact_name, profile.emergency_contact_phone]
    complete_count = sum(bool(value) for value in complete_fields)
    return render(request, "accounts/profile.html", {"form": form, "password_form": password_form, "profile": profile, "profile_completion": round(complete_count * 100 / len(complete_fields)), "title": "My profile"})


@login_required
def password_change_view(request):
    if request.method == "GET":
        return redirect("/profile/#security")
    return profile_view(request)


@login_required
def user_create_view(request):
    actor_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if actor_profile.role not in (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER):
        messages.error(request, "You do not have permission to create users.")
        return redirect("dashboard")
    form = ManagedUserForm(request.POST or None, actor_profile=actor_profile)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        UserActivity.objects.create(user=request.user, action="user_created", details={"target": user.username, "role": user.profile.role}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, f"Account created for {user.username}.")
        return redirect("dashboard")
    return render(request, "accounts/form_page.html", {"form": form, "title": "Create user", "button": "Create user"})


@login_required
def user_management_view(request):
    actor_profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE},
    )
    if not request.user.is_superuser and actor_profile.role != UserProfile.Role.SUPER_ADMIN:
        messages.error(request, "Only a system admin can manage user roles.")
        return redirect("dashboard")

    User = get_user_model()
    all_users = User.objects.only("pk", "is_superuser").iterator()
    for user in all_users:
        UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE
            },
        )

    users = User.objects.all()
    search = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()
    if search:
        users = users.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )
    if role_filter in UserProfile.Role.values:
        users = users.filter(profile__role=role_filter)
    if status_filter == "active":
        users = users.filter(is_active=True)
    elif status_filter == "inactive":
        users = users.filter(is_active=False)

    user_count = User.objects.count()
    active_user_count = User.objects.filter(is_active=True).count()
    unassigned_user_count = UserProfile.objects.filter(
        role=UserProfile.Role.EMPLOYEE,
        company__isnull=True,
        factory__isnull=True,
        department__isnull=True,
    ).count()
    profiles = UserProfile.objects.filter(user__in=users).select_related(
        "user", "company", "factory", "department", "user__employee_record"
    ).order_by("user__first_name", "user__last_name", "user__username")
    page_obj = Paginator(profiles, 25).get_page(request.GET.get("page"))
    user_rows = [
        {
            "profile": profile,
            "form": UserAssignmentForm(instance=profile),
            "protected": profile.user_id == request.user.pk or profile.user.is_superuser,
            "employee": getattr(profile.user, "employee_record", None),
        }
        for profile in page_obj.object_list
    ]
    return render(request, "accounts/user_management.html", {
        "user_rows": user_rows,
        "page_obj": page_obj,
        "search": search,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "role_choices": UserProfile.Role.choices,
        "user_count": user_count,
        "active_user_count": active_user_count,
        "unassigned_user_count": unassigned_user_count,
        "companies": Company.objects.order_by("name"),
        "factories": Factory.objects.select_related("company").order_by("company__name", "name"),
        "departments": Department.objects.select_related("factory").order_by("factory__name", "name"),
        "title": "Manage users & roles",
    })


@login_required
@require_POST
def user_assignment_view(request, profile_id):
    actor_profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE},
    )
    if not request.user.is_superuser and actor_profile.role != UserProfile.Role.SUPER_ADMIN:
        messages.error(request, "Only a system admin can assign user roles.")
        return redirect("dashboard")

    profile = UserProfile.objects.select_related("user").get(pk=profile_id)
    if profile.user_id == request.user.pk or profile.user.is_superuser:
        messages.error(request, "Your own role and system superuser accounts cannot be changed here.")
        return redirect("user_management")

    form = UserAssignmentForm(request.POST, instance=profile)
    if form.is_valid():
        with transaction.atomic():
            profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
            old_assignment = {
                "role": profile.role,
                "company": profile.company_id,
                "factory": profile.factory_id,
                "department": profile.department_id,
            }
            updated_profile = form.save()
            UserActivity.objects.create(
                user=request.user,
                action="user_role_assigned",
                details={
                    "target": profile.user.username,
                    "previous": old_assignment,
                    "current": {
                        "role": updated_profile.role,
                        "company": updated_profile.company_id,
                        "factory": updated_profile.factory_id,
                        "department": updated_profile.department_id,
                    },
                },
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        messages.success(request, f"Access updated for {profile.user.username}.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    return redirect("user_management")


@login_required
def audit_history_view(request):
    if not request.user.is_superuser and getattr(request.user, "profile", None) and request.user.profile.role != UserProfile.Role.SUPER_ADMIN:
        messages.error(request, "You do not have permission to view audit history.")
        return redirect("dashboard")
    events = UserActivity.objects.select_related("user")
    username = request.GET.get("user", "").strip()
    if username:
        events = events.filter(Q(user__username__icontains=username))
    page_obj = Paginator(events, 50).get_page(request.GET.get("page"))
    return render(request, "accounts/audit_history.html", {"activities": page_obj, "page_obj": page_obj, "username": username, "event_count": events.count()})




