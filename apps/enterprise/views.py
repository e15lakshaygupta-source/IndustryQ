import hashlib
import json
import secrets
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import UserActivity, UserProfile
from apps.core.backup_utils import create_backup, stage_backup
from apps.operations.models import BillingDocument, InventoryItem, ProcurementDocument
from apps.operations.views import inventory_balance
from apps.organization.models import Company, Factory
from apps.workforce.models import Employee
from apps.intelligence.views import can_manage_assets, can_manage_quality
from django.contrib.auth import get_user_model
from .forms import ComplianceItemForm, DocumentUploadForm, IndustrialProjectForm, IntegrationAppForm, PortalAccessForm, ProjectTaskForm
from .models import ComplianceItem, DocumentVersion, IndustrialProject, IntegrationApp, ManagedDocument, PortalAccess, ProjectTask, RecoveryTest

MANAGER_ROLES = (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER)


def workspace(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE})
    companies, factories = Company.objects.all(), Factory.objects.all()
    if user.is_superuser: profile.role = UserProfile.Role.SUPER_ADMIN
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        companies = companies.filter(factories__in=factories).distinct()
    return profile, companies, factories


def manager_only(profile):
    if profile.role not in MANAGER_ROLES: raise Http404


@login_required
def enterprise_home(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    return render(request, "enterprise/home.html", {"profile": profile, "companies": companies, "factories": factories, "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
def documents(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    form = DocumentUploadForm(request.POST or None, request.FILES or None, companies=companies, factories=factories) if can_manage else None
    if request.method == "POST" and form and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            document, _ = ManagedDocument.objects.get_or_create(title=data["title"], category=data["category"], company=data["company"], factory=data["factory"], defaults={"uploaded_by": request.user})
            document = ManagedDocument.objects.select_for_update().get(pk=document.pk)
            next_version = (document.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1
            DocumentVersion.objects.create(document=document, version_number=next_version, file=data["file"], notes=data["notes"], uploaded_by=request.user)
            document.updated_at = timezone.now(); document.save(update_fields=["updated_at"])
        UserActivity.objects.create(user=request.user, action="document_version_uploaded", details={"document_id": document.pk, "version": next_version})
        messages.success(request, f"Document version {next_version} uploaded.")
        return redirect("enterprise:documents")
    docs = ManagedDocument.objects.filter(factory__in=factories) | ManagedDocument.objects.filter(company__in=companies, factory__isnull=True)
    return render(request, "enterprise/documents.html", {"form": form, "documents": docs.distinct().prefetch_related("versions__uploaded_by")[:100], "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
def document_download(request, pk):
    profile, companies, factories = workspace(request.user)
    version = get_object_or_404(DocumentVersion.objects.select_related("document"), pk=pk, document__in=(ManagedDocument.objects.filter(factory__in=factories) | ManagedDocument.objects.filter(company__in=companies, factory__isnull=True)))
    try: return FileResponse(version.file.open("rb"), as_attachment=True, filename=Path(version.file.name).name)
    except OSError: raise Http404


@login_required
def compliance(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    form = ComplianceItemForm(request.POST or None, request.FILES or None, factories=factories) if can_manage else None
    if request.method == "POST" and form and form.is_valid():
        item = form.save(commit=False); item.created_by = request.user
        if item.due_date and item.due_date < timezone.localdate() and item.status != ComplianceItem.Status.COMPLETE: item.status = ComplianceItem.Status.OVERDUE
        item.save()
        UserActivity.objects.create(user=request.user, action="compliance_item_created", details={"compliance_id": item.pk})
        messages.success(request, "Compliance / risk item saved.")
        return redirect("enterprise:compliance")
    items = ComplianceItem.objects.filter(factory__in=factories).select_related("factory", "owner")
    today = timezone.localdate()
    items.filter(due_date__lt=today).exclude(status__in=[ComplianceItem.Status.COMPLETE, ComplianceItem.Status.OVERDUE]).update(status=ComplianceItem.Status.OVERDUE, updated_at=timezone.now())
    return render(request, "enterprise/compliance.html", {"form": form, "items": items[:200], "overdue_count": items.filter(status=ComplianceItem.Status.OVERDUE).count(), "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
@require_POST
def compliance_status(request, pk):
    profile, companies, factories = workspace(request.user)
    manager_only(profile)
    item = get_object_or_404(ComplianceItem, pk=pk, factory__in=factories)
    status = request.POST.get("status")
    if status in ComplianceItem.Status.values:
        item.status = status
        if item.due_date and item.due_date < timezone.localdate() and status != ComplianceItem.Status.COMPLETE: item.status = ComplianceItem.Status.OVERDUE
        item.save(update_fields=["status", "updated_at"])
        messages.success(request, "Compliance status updated.")
    return redirect("enterprise:compliance")


@login_required
def projects(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    project_form = IndustrialProjectForm(request.POST if can_manage and request.POST.get("action") == "project" else None, factories=factories) if can_manage else None
    task_form = ProjectTaskForm(request.POST if can_manage and request.POST.get("action") == "task" else None, factories=factories) if can_manage else None
    if request.method == "POST":
        action = request.POST.get("action")
        form = project_form if can_manage and action == "project" else task_form if can_manage and action == "task" else None
        if form and form.is_valid():
            record = form.save(commit=False)
            if action == "project": record.created_by = request.user
            record.save()
            messages.success(request, "Project saved." if action == "project" else "Project task / milestone saved.")
            return redirect("enterprise:projects")
    project_rows = IndustrialProject.objects.filter(factory__in=factories).select_related("factory", "manager").prefetch_related("tasks__assignee").annotate(task_count=Count("tasks", distinct=True), done_task_count=Count("tasks", filter=Q(tasks__status=ProjectTask.Status.DONE), distinct=True))
    project_rows = project_rows[:100]
    return render(request, "enterprise/projects.html", {"project_form": project_form, "task_form": task_form, "projects": project_rows, "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile), "total_project_count": IndustrialProject.objects.filter(factory__in=factories).count(), "active_project_count": IndustrialProject.objects.filter(factory__in=factories, status=IndustrialProject.Status.ACTIVE).count(), "open_task_count": ProjectTask.objects.filter(project__factory__in=factories).exclude(status=ProjectTask.Status.DONE).count(), "milestone_count": ProjectTask.objects.filter(project__factory__in=factories, milestone=True).exclude(status=ProjectTask.Status.DONE).count(), "project_factory_count": factories.count()})


@login_required
@require_POST
def project_task_status(request, pk):
    profile, companies, factories = workspace(request.user)
    manager_only(profile)
    task = get_object_or_404(ProjectTask, pk=pk, project__factory__in=factories)
    if request.POST.get("status") in ProjectTask.Status.values:
        task.status = request.POST["status"]; task.save(update_fields=["status"])
        messages.success(request, "Task progress updated.")
    return redirect("enterprise:projects")


@login_required
def external_portal(request):
    access = PortalAccess.objects.filter(user=request.user, is_active=True).select_related("customer__company", "supplier__factory").first()
    if not access: raise Http404
    if access.party_type == PortalAccess.PartyType.CUSTOMER and access.customer_id:
        invoices = BillingDocument.objects.filter(customer=access.customer, kind=BillingDocument.Kind.SALES_INVOICE).prefetch_related("lines")
        return render(request, "enterprise/external_portal.html", {"access": access, "invoices": invoices[:100], "can_manage": False, "can_manage_assets": False, "can_manage_quality": False})
    if access.party_type != PortalAccess.PartyType.SUPPLIER or not access.supplier_id: raise Http404
    orders = ProcurementDocument.objects.filter(supplier=access.supplier).select_related("item", "warehouse", "factory")
    bills = BillingDocument.objects.filter(supplier=access.supplier, kind=BillingDocument.Kind.SUPPLIER_BILL)
    return render(request, "enterprise/external_portal.html", {"access": access, "orders": orders[:100], "bills": bills[:100], "can_manage": False, "can_manage_assets": False, "can_manage_quality": False})


@login_required
def portal_admin(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    form = PortalAccessForm(request.POST or None, factories=factories, companies=companies) if can_manage else None
    if request.method == "POST" and can_manage and form and form.is_valid():
        access = form.save(commit=False)
        if access.party_type == PortalAccess.PartyType.CUSTOMER and (not access.customer_id or access.customer.company_id not in companies.values_list("pk", flat=True)): raise Http404
        if access.party_type == PortalAccess.PartyType.SUPPLIER and (not access.supplier_id or access.supplier.factory_id not in factories.values_list("pk", flat=True)): raise Http404
        access.save()
        UserActivity.objects.create(user=request.user, action="external_portal_access_saved", details={"portal_access_id": access.pk, "party_type": access.party_type})
        messages.success(request, "External portal account linked.")
        return redirect("enterprise:portal_admin")
    accesses = PortalAccess.objects.none()
    if can_manage:
        accesses = PortalAccess.objects.filter(customer__company__in=companies).select_related("user", "customer", "supplier") | PortalAccess.objects.filter(supplier__factory__in=factories).select_related("user", "customer", "supplier")
    return render(request, "enterprise/portal_admin.html", {"form": form, "accesses": accesses.distinct(), "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
def mobile_workforce(request):
    profile, companies, factories = workspace(request.user)
    employee = Employee.objects.filter(user=request.user).first()
    if profile.role == UserProfile.Role.EMPLOYEE and not employee: raise Http404
    return render(request, "enterprise/mobile.html", {"employee": employee, "profile": profile, "can_manage_quality": can_manage_quality(profile)})


@login_required
def integrations(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    form = IntegrationAppForm(request.POST or None, factories=factories) if can_manage else None
    if request.method == "POST" and form and form.is_valid():
        token = secrets.token_urlsafe(36)
        app = form.save(commit=False); app.token_hash = hashlib.sha256(token.encode()).hexdigest(); app.token_prefix = token[:10]; app.created_by = request.user; app.save()
        request.session["new_integration_token"] = token
        UserActivity.objects.create(user=request.user, action="integration_created", details={"integration_id": app.pk, "factory_id": app.factory_id})
        messages.success(request, "Integration created. Copy the API key now; it will only be shown once.")
        return redirect("enterprise:integrations")
    token = request.session.pop("new_integration_token", None)
    apps = IntegrationApp.objects.filter(factory__in=factories)
    return render(request, "enterprise/integrations.html", {"form": form, "integrations": apps, "new_token": token, "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
@require_POST
def integration_toggle(request, pk):
    profile, companies, factories = workspace(request.user)
    manager_only(profile)
    app = get_object_or_404(IntegrationApp, pk=pk, factory__in=factories)
    app.is_active = not app.is_active; app.save(update_fields=["is_active"])
    messages.success(request, f"Integration {'enabled' if app.is_active else 'revoked'}.")
    return redirect("enterprise:integrations")


@require_GET
def integration_summary_api(request, public_id):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    if not token: return JsonResponse({"detail": "Bearer API key required."}, status=401)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app = IntegrationApp.objects.filter(public_id=public_id, token_hash=token_hash, is_active=True).select_related("factory").first()
    if not app: return JsonResponse({"detail": "Invalid or revoked API key."}, status=403)
    app.last_used_at = timezone.now(); app.save(update_fields=["last_used_at"])
    items = InventoryItem.objects.filter(factory=app.factory, is_active=True)
    inventory = []
    for item in items:
        on_hand = inventory_balance(item)
        inventory.append({"sku": item.sku, "name": item.name, "on_hand": str(on_hand), "minimum": str(item.minimum_stock), "unit": item.unit_of_measure, "low_stock": item.minimum_stock > 0 and on_hand <= item.minimum_stock})
    orders = ProcurementDocument.objects.filter(factory=app.factory).exclude(status__in=[ProcurementDocument.Status.RECEIVED, ProcurementDocument.Status.CANCELLED]).count()
    invoices = BillingDocument.objects.filter(factory=app.factory, kind=BillingDocument.Kind.SALES_INVOICE).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID]).count()
    return JsonResponse({"factory": app.factory.name, "generated_at": timezone.now().isoformat(), "open_procurement_orders": orders, "open_sales_invoices": invoices, "inventory_item_count": len(inventory), "low_stock_item_count": sum(item["low_stock"] for item in inventory), "inventory": inventory})


@login_required
def recovery_center(request):
    profile, companies, factories = workspace(request.user)
    can_manage = profile.role in MANAGER_ROLES
    if request.method == "POST" and not can_manage: raise Http404
    if request.method == "POST" and request.POST.get("action") == "backup":
        path = create_backup()
        messages.success(request, f"Backup created: {path.name}")
        UserActivity.objects.create(user=request.user, action="backup_created", details={"filename": path.name})
        return redirect("enterprise:recovery")
    if request.method == "POST" and request.POST.get("action") != "verify": raise Http404
    if request.method == "POST" and request.POST.get("action") == "verify":
        filename = Path(request.POST.get("filename", "")).name
        backup_dir = (settings.BASE_DIR / "backups").resolve()
        archive = (backup_dir / filename).resolve()
        try:
            archive.relative_to(backup_dir)
            if not filename.startswith("industryos-backup-") or not archive.is_file(): raise ValueError("Select an existing INDUSTRIQ backup archive.")
            with tempfile.TemporaryDirectory() as staging:
                database_path, media_path, manifest = stage_backup(archive, Path(staging))
                detail = f"Archive structure and checksums validated. Database fixture: {Path(database_path).stat().st_size} bytes; media files: {len(manifest.get('media_files', []))}. Data was staged for verification; live database was not modified."
            RecoveryTest.objects.create(archive_name=filename, passed=True, result=detail, tested_by=request.user)
            messages.success(request, "Backup validation passed. " + detail)
        except Exception as exc:
            RecoveryTest.objects.create(archive_name=filename[:255], passed=False, result=str(exc), tested_by=request.user)
            messages.error(request, f"Backup validation failed: {exc}")
        return redirect("enterprise:recovery")
    backup_dir = settings.BASE_DIR / "backups"
    files = sorted((path for path in backup_dir.glob("industryos-backup-*.zip") if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    return render(request, "enterprise/recovery.html", {"backups": [{"name": path.name, "size": path.stat().st_size, "modified": timezone.datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())} for path in files[:50]], "results": RecoveryTest.objects.select_related("tested_by")[:30], "can_manage": can_manage, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@login_required
def security_center(request):
    profile, companies, factories = workspace(request.user)
    is_super_admin = profile.role == UserProfile.Role.SUPER_ADMIN
    return render(request, "enterprise/security.html", {"profile": profile, "can_view_audit": is_super_admin, "can_manage_users": is_super_admin, "mfa_configured": False, "sso_configured": False, "can_manage": profile.role in MANAGER_ROLES, "can_manage_assets": can_manage_assets(profile), "can_manage_quality": can_manage_quality(profile)})


@require_GET
def service_worker(request):
    script = "const CACHE='industryos-shell-v1';self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/static/css/style.css']))));self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method==='GET'&&u.origin===location.origin&&u.pathname.startsWith('/static/'))e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));});"
    response = HttpResponse(script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response










