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
from apps.intelligence.models import Machine
from apps.enterprise.models import IndustrialProject
from apps.enterprise.models import ComplianceItem


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
    return redirect("core:executive_dashboard")


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




