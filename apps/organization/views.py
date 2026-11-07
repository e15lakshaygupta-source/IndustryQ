from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import UserActivity, UserProfile
from .forms import CompanyForm, FactoryForm, DepartmentForm, ProductionUnitForm
from .models import Company, Factory, Department, ProductionUnit


ROLE_ORDER = {UserProfile.Role.SUPER_ADMIN: 4, UserProfile.Role.COMPANY_ADMIN: 3,
              UserProfile.Role.FACTORY_MANAGER: 2, UserProfile.Role.DEPARTMENT_MANAGER: 1,
              UserProfile.Role.EMPLOYEE: 0}
CONFIG = {
    "company": {"model": Company, "form": CompanyForm, "title": "Companies", "search": ("name", "registration_number", "industry_type", "city")},
    "factory": {"model": Factory, "form": FactoryForm, "title": "Factories", "search": ("name", "factory_code", "company__name", "city")},
    "department": {"model": Department, "form": DepartmentForm, "title": "Departments", "search": ("name", "department_code", "factory__name", "manager_name")},
    "unit": {"model": ProductionUnit, "form": ProductionUnitForm, "title": "Production units", "search": ("name", "unit_code", "department__name")},
}


def access_context(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE})
    if request.user.is_superuser and profile.role != UserProfile.Role.SUPER_ADMIN:
        profile.role = UserProfile.Role.SUPER_ADMIN
        profile.save(update_fields=["role", "updated_at"])
    role = profile.role
    companies, factories, departments = Company.objects.all(), Factory.objects.all(), Department.objects.all()
    if role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies, company__is_active=True)
        companies = companies.filter(pk__in=factories.values("company_id"))
        departments = departments.filter(factory__in=factories)
    elif role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER):
        factories = factories.filter(pk=profile.factory_id)
        companies = companies.filter(pk=profile.factory.company_id) if profile.factory_id else companies.none()
        departments = departments.filter(factory_id=profile.factory_id)
        if role == UserProfile.Role.DEPARTMENT_MANAGER:
            departments = departments.filter(pk=profile.department_id)
    return profile, companies, factories, departments


def allowed_queryset(kind, request, context):
    profile, companies, factories, departments = context
    model = CONFIG[kind]["model"]
    if kind == "company": return companies
    if kind == "factory": return factories
    if kind == "department": return departments
    qs = model.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN: return qs.filter(department__factory__in=factories)
    if profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER): return qs.filter(department__in=departments if profile.role == UserProfile.Role.DEPARTMENT_MANAGER else Department.objects.filter(factory=profile.factory))
    return qs


def can_manage(kind, context):
    role = context[0].role
    minimum = {"company": 4, "factory": 3, "department": 2, "unit": 1}[kind]
    return ROLE_ORDER.get(role, 0) >= minimum


def record_or_404(kind, pk, context):
    obj = get_object_or_404(allowed_queryset(kind, None, context), pk=pk)
    return obj


@login_required
def organization_overview(request):
    context = access_context(request)
    profile, companies, factories, departments = context
    units = ProductionUnit.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN: units = units.filter(department__factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER): units = units.filter(department__in=departments if profile.role == UserProfile.Role.DEPARTMENT_MANAGER else Department.objects.filter(factory=profile.factory))
    return render(request, "organization/overview.html", {"companies": companies, "factories": factories, "departments": departments, "units": units, "company_count": companies.count(), "factory_count": factories.count(), "department_count": departments.count(), "unit_count": units.count()})


@login_required
def object_list(request, kind):
    config = CONFIG[kind]
    context = access_context(request)
    queryset = allowed_queryset(kind, request, context).select_related()
    term = request.GET.get("q", "").strip()
    if term:
        predicate = Q()
        for field in config["search"]: predicate |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(predicate)
    page = Paginator(queryset, 15).get_page(request.GET.get("page"))
    return render(request, "organization/object_list.html", {"kind": kind, "title": config["title"], "page_obj": page, "search_query": term, "can_manage": can_manage(kind, context)})


@login_required
def object_form(request, kind, pk=None):
    config = CONFIG[kind]
    context = access_context(request)
    if not can_manage(kind, context): raise Http404
    instance = record_or_404(kind, pk, context) if pk is not None else None
    company_scope = context[1]
    factory_scope = context[2]
    department_scope = context[3]
    if kind == "factory": form = config["form"](request.POST or None, instance=instance, allowed_companies=company_scope)
    elif kind == "department": form = config["form"](request.POST or None, instance=instance, allowed_factories=factory_scope)
    elif kind == "unit": form = config["form"](request.POST or None, instance=instance, allowed_departments=department_scope if context[0].role == UserProfile.Role.DEPARTMENT_MANAGER else (Department.objects.filter(factory=context[0].factory) if context[0].role == UserProfile.Role.FACTORY_MANAGER else department_scope))
    else: form = config["form"](request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        UserActivity.objects.create(user=request.user, action=f"organization_{kind}_{'updated' if instance else 'created'}", details={"id": obj.pk, "name": str(obj)}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, f"{config['title'][:-1]} saved successfully.")
        return redirect(f"organization_{kind}_list")
    return render(request, "organization/object_form.html", {"form": form, "title": f"{'Edit' if instance else 'Add'} {config['title'][:-1]}", "kind": kind, "instance": instance})


@login_required
@require_POST
def object_delete(request, kind, pk):
    config = CONFIG[kind]
    context = access_context(request)
    if not can_manage(kind, context): raise Http404
    obj = record_or_404(kind, pk, context)
    name, object_id = str(obj), obj.pk
    obj.delete()
    UserActivity.objects.create(user=request.user, action=f"organization_{kind}_deleted", details={"id": object_id, "name": name}, ip_address=request.META.get("REMOTE_ADDR"))
    messages.success(request, f"{name} deleted.")
    return redirect(f"organization_{kind}_list")
