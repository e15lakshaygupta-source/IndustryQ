import json
from datetime import timedelta
from decimal import Decimal

from PIL import Image, ImageFilter, ImageStat

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count, Case, When, F, DecimalField
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Notification, UserActivity, UserProfile
from apps.organization.models import Company, Factory, Department
from apps.workforce.models import Attendance, Employee, LeaveRequest
from apps.operations.models import BillingDocument, InventoryItem, ProcurementDocument, ProductionEntry, StockMovement, Supplier, WorkOrder
from apps.operations.views import inventory_balance
from apps.workforce.models import LeaveRequest
from .forms import EnergyReadingForm, MachineForm, MaintenanceRecordForm, QualityInspectionForm, SafetyIncidentForm, SustainabilityReadingForm, WorkflowRuleForm
from .models import AssistantQuery, EnergyReading, Machine, MaintenanceRecord, QualityInspection, SafetyIncident, SustainabilityReading, WorkflowAlert, WorkflowRule


def workspace_scope(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE})
    if user.is_superuser and profile.role != UserProfile.Role.SUPER_ADMIN:
        profile.role = UserProfile.Role.SUPER_ADMIN
        profile.save(update_fields=["role", "updated_at"])
    companies, factories, departments = Company.objects.all(), Factory.objects.all(), Department.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
        departments = departments.filter(factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        departments = departments.filter(factory_id=profile.factory_id)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): departments = departments.filter(pk=profile.department_id)
    return profile, companies, factories, departments


def can_manage_quality(profile):
    return profile.role in (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER)


def can_manage_assets(profile):
    return can_manage_quality(profile)


def scoped_machines(profile, factories, departments):
    machines = Machine.objects.filter(factory__in=factories).select_related("factory", "department", "production_unit")
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        machines = machines.filter(department__in=departments)
    return machines


@login_required
def intelligence_home(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    inspections = QualityInspection.objects.filter(factory__in=factories).select_related("factory", "department", "inspected_by")
    readings = EnergyReading.objects.filter(factory__in=factories).select_related("factory", "department")
    today = timezone.localdate()
    today_energy = readings.filter(reading_at__date=today).aggregate(total=Sum("consumption_kwh"))["total"]
    machines = scoped_machines(profile, factories, departments)
    maintenance = MaintenanceRecord.objects.filter(machine__in=machines)
    incidents = SafetyIncident.objects.filter(factory__in=factories)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): incidents = incidents.filter(department__in=departments)
    context = {"profile": profile, "inspection_count": inspections.count(), "pending_inspections": inspections.filter(status=QualityInspection.Status.PENDING).count(), "failed_inspections": inspections.filter(status=QualityInspection.Status.FAIL).count(), "recent_inspections": inspections[:8], "energy_today": today_energy, "energy_readings": readings[:8], "can_manage": can_manage_quality(profile), "machine_count": machines.count(), "maintenance_open": maintenance.exclude(status=MaintenanceRecord.Status.COMPLETED).count(), "incident_open": incidents.exclude(status=SafetyIncident.Status.RESOLVED).count()}
    return render(request, "intelligence/home.html", context)


@login_required
def machine_home(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    machines = scoped_machines(profile, factories, departments)
    maintenance = MaintenanceRecord.objects.filter(machine__in=machines).select_related("machine", "machine__factory", "machine__department", "reported_by")
    return render(request, "intelligence/machines.html", {"machines": machines, "replacement_machines": machines.filter(replacement_date__isnull=False).order_by("replacement_date")[:30], "maintenance": maintenance[:30], "machine_count": machines.count(), "operational_count": machines.filter(status=Machine.Status.OPERATIONAL).count(), "attention_count": machines.exclude(status__in=[Machine.Status.OPERATIONAL, Machine.Status.INACTIVE]).count(), "can_manage": can_manage_assets(profile)})


@login_required
def predictive_maintenance(request):
    """Explainable maintenance risk indicators based on recorded asset history."""
    profile, companies, factories, departments = workspace_scope(request.user)
    today = timezone.localdate()
    history_start = today - timedelta(days=180)
    machines = scoped_machines(profile, factories, departments).prefetch_related("maintenance_records")
    rows = []
    for machine in machines:
        records = list(machine.maintenance_records.all())
        breakdowns = [record for record in records if record.kind == MaintenanceRecord.Kind.BREAKDOWN]
        recent_breakdowns = [record for record in breakdowns if record.created_at.date() >= history_start]
        open_records = [record for record in records if record.status != MaintenanceRecord.Status.COMPLETED]
        overdue = [record for record in open_records if record.scheduled_for and record.scheduled_for < today]
        upcoming = [record for record in open_records if record.scheduled_for and today <= record.scheduled_for <= today + timedelta(days=7)]

        signals = []
        if machine.status == Machine.Status.BREAKDOWN:
            score = 80
            signals.append("Machine is marked as broken down")
        elif machine.status == Machine.Status.MAINTENANCE:
            score = 38
            signals.append("Machine is marked maintenance due")
        else:
            score = 8
        score += min(35, len(recent_breakdowns) * 15)
        if recent_breakdowns:
            signals.append(f"{len(recent_breakdowns)} breakdown record(s) in the last 180 days")
        if overdue:
            score += 22
            signals.append(f"{len(overdue)} scheduled job(s) overdue")
        elif upcoming:
            score += 8
            signals.append("Scheduled maintenance due within 7 days")
        if len(recent_breakdowns) >= 2:
            signals.append("Repeat breakdown pattern flagged for review")
        score = min(100, score)

        if machine.status == Machine.Status.INACTIVE:
            band, risk_label, recommendation_date = "paused", "Not scored", None
        elif score >= 60:
            band, risk_label, recommendation_date = "high", "High", today + timedelta(days=1)
        elif score >= 30:
            band, risk_label, recommendation_date = "watch", "Watch", today + timedelta(days=7)
        else:
            band, risk_label, recommendation_date = "low", "Low", today + timedelta(days=30)
        if not signals and machine.status != Machine.Status.INACTIVE:
            signals.append("No recent breakdown or overdue maintenance recorded")
        rows.append({
            "machine": machine, "score": score, "band": band, "risk_label": risk_label,
            "signals": signals, "recent_breakdowns": len(recent_breakdowns),
            "open_count": len(open_records), "overdue_count": len(overdue),
            "recommendation_date": recommendation_date,
        })
    rows.sort(key=lambda row: (row["band"] == "paused", -row["score"], row["machine"].asset_tag))
    context = {
        "rows": rows, "machine_count": len(rows),
        "high_count": sum(row["band"] == "high" for row in rows),
        "watch_count": sum(row["band"] == "watch" for row in rows),
        "recommended_count": sum(row["recommendation_date"] is not None for row in rows),
        "overdue_count": sum(row["overdue_count"] for row in rows),
        "today": today, "can_manage": can_manage_assets(profile),
    }
    return render(request, "intelligence/predictive_maintenance.html", context)


@login_required
def machine_create(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_assets(profile):
        messages.error(request, "You do not have permission to register machines.")
        return redirect("intelligence:machines")
    form = MachineForm(request.POST or None, allowed_factories=factories, allowed_departments=departments)
    if request.method == "POST" and form.is_valid():
        machine = form.save()
        UserActivity.objects.create(user=request.user, action="machine_registered", details={"machine_id": machine.pk, "asset_tag": machine.asset_tag}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Machine registered.")
        return redirect("intelligence:machines")
    return render(request, "intelligence/form.html", {"form": form, "title": "Register machine", "button": "Register machine"})


@login_required
def machine_edit(request, pk):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_assets(profile):
        messages.error(request, "You do not have permission to update machine records.")
        return redirect("intelligence:machines")
    machine = scoped_machines(profile, factories, departments).filter(pk=pk).first()
    if not machine: return redirect("intelligence:machines")
    form = MachineForm(request.POST or None, instance=machine, allowed_factories=factories, allowed_departments=departments)
    if request.method == "POST" and form.is_valid():
        form.save()
        UserActivity.objects.create(user=request.user, action="machine_updated", details={"machine_id": machine.pk, "status": machine.status}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Machine details and status updated.")
        return redirect("intelligence:machines")
    return render(request, "intelligence/form.html", {"form": form, "title": f"Update machine {machine.asset_tag}", "button": "Save machine"})


@login_required
def maintenance_create(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    machines = scoped_machines(profile, factories, departments)
    if not can_manage_assets(profile):
        messages.error(request, "You do not have permission to manage maintenance records.")
        return redirect("intelligence:machines")
    initial = {}
    if request.method != "POST":
        machine_id = request.GET.get("machine")
        if machine_id and machines.filter(pk=machine_id).exists():
            initial = {"machine": machine_id, "summary": "Predictive maintenance inspection"}
            scheduled_for = request.GET.get("scheduled_for", "")
            try:
                from datetime import date
                parsed_date = date.fromisoformat(scheduled_for)
                if parsed_date >= timezone.localdate(): initial["scheduled_for"] = parsed_date
            except (TypeError, ValueError):
                pass
    form = MaintenanceRecordForm(request.POST or None, allowed_machines=machines, initial=initial)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.reported_by = request.user
        if record.status == MaintenanceRecord.Status.COMPLETED: record.completed_at = timezone.now()
        record.save()
        if record.kind == MaintenanceRecord.Kind.BREAKDOWN and record.status != MaintenanceRecord.Status.COMPLETED:
            record.machine.status = Machine.Status.BREAKDOWN
            record.machine.save(update_fields=["status"])
        UserActivity.objects.create(user=request.user, action="maintenance_record_created", details={"record_id": record.pk, "kind": record.kind}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Maintenance record saved.")
        return redirect("intelligence:predictive_maintenance")
    return render(request, "intelligence/form.html", {"form": form, "title": "Schedule maintenance or report breakdown", "button": "Save maintenance record"})


@login_required
def safety_incidents(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    incidents = SafetyIncident.objects.filter(factory__in=factories).select_related("factory", "department", "reported_by")
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): incidents = incidents.filter(department__in=departments)
    form = SafetyIncidentForm(request.POST or None, allowed_factories=factories, allowed_departments=departments)
    if request.method == "POST" and form.is_valid():
        incident = form.save(commit=False)
        incident.reported_by = request.user
        incident.save()
        UserActivity.objects.create(user=request.user, action="safety_incident_reported", details={"incident_id": incident.pk, "severity": incident.severity}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Safety incident recorded.")
        return redirect("intelligence:safety_incidents")
    return render(request, "intelligence/safety_incidents.html", {"form": form, "incidents": incidents[:50], "open_count": incidents.exclude(status=SafetyIncident.Status.RESOLVED).count()})


@login_required
def inspection_create(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile):
        messages.error(request, "You do not have permission to submit inspections.")
        return redirect("intelligence:home")
    allowed_depts = departments
    form = QualityInspectionForm(request.POST or None, request.FILES or None, allowed_factories=factories, allowed_departments=allowed_depts)
    if request.method == "POST" and form.is_valid():
        inspection = form.save(commit=False)
        inspection.inspected_by = request.user
        inspection.save()
        with Image.open(inspection.image.path) as source:
            image = source.convert("L")
            image.thumbnail((512, 512))
            brightness = ImageStat.Stat(image).mean[0]
            edge_detail = ImageStat.Stat(image.filter(ImageFilter.FIND_EDGES)).mean[0]
        inspection.image_brightness = Decimal(str(round(brightness, 2)))
        inspection.image_edge_detail = Decimal(str(round(edge_detail, 2)))
        inspection.image_quality_flag = brightness < 35 or brightness > 225 or edge_detail < 3
        inspection.save(update_fields=["image_brightness", "image_edge_detail", "image_quality_flag"])
        UserActivity.objects.create(user=request.user, action="quality_inspection_submitted", details={"inspection_id": inspection.pk}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Image analyzed for exposure and edge detail, then submitted for human defect review.")
        return redirect("intelligence:home")
    return render(request, "intelligence/form.html", {"form": form, "title": "Submit quality inspection", "button": "Submit image"})


@login_required
def inspection_history(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    inspections = QualityInspection.objects.filter(factory__in=factories).select_related("factory", "department", "inspected_by")
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        inspections = inspections.filter(department__in=departments)
    status = request.GET.get("status", "")
    if status in QualityInspection.Status.values: inspections = inspections.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query: inspections = inspections.filter(Q(product_reference__icontains=query) | Q(defect_label__icontains=query))
    page = Paginator(inspections, 25).get_page(request.GET.get("page"))
    return render(request, "intelligence/inspection_history.html", {"page": page, "status": status, "query": query, "can_manage": can_manage_quality(profile)})


@login_required
def inspection_review(request, pk):
    profile, companies, factories, departments = workspace_scope(request.user)
    inspection = QualityInspection.objects.filter(factory__in=factories).select_related("factory", "department").filter(pk=pk).first()
    if not inspection: return redirect("intelligence:home")
    if not can_manage_quality(profile):
        messages.error(request, "You do not have permission to review inspections.")
        return redirect("intelligence:home")
    if request.method == "POST":
        status = request.POST.get("status")
        if status in (QualityInspection.Status.PASS, QualityInspection.Status.FAIL):
            inspection.status = status
            inspection.defect_label = request.POST.get("defect_label", "")[:150]
            inspection.notes = request.POST.get("notes", inspection.notes)
            inspection.save(update_fields=["status", "defect_label", "notes"])
            UserActivity.objects.create(user=request.user, action="quality_inspection_reviewed", details={"inspection_id": inspection.pk, "status": status}, ip_address=request.META.get("REMOTE_ADDR"))
            messages.success(request, "Inspection review saved.")
            return redirect("intelligence:home")
        messages.error(request, "Choose Pass or Fail.")
    return render(request, "intelligence/review.html", {"inspection": inspection})


@login_required
def energy_reading_create(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile):
        messages.error(request, "You do not have permission to record energy readings.")
        return redirect("intelligence:home")
    form = EnergyReadingForm(request.POST or None, allowed_factories=factories, allowed_departments=departments)
    if request.method == "POST" and form.is_valid():
        reading = form.save(commit=False)
        reading.recorded_by = request.user
        reading.save()
        UserActivity.objects.create(user=request.user, action="energy_reading_recorded", details={"reading_id": reading.pk}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Electricity meter reading recorded.")
        return redirect("intelligence:sustainability")
    return render(request, "intelligence/form.html", {"form": form, "title": "Record energy meter reading", "button": "Save reading"})


@login_required
def sustainability_dashboard(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    readings = SustainabilityReading.objects.filter(factory__in=factories).select_related("factory", "department", "recorded_by")
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        readings = readings.filter(department__in=departments)
    today = timezone.localdate()
    current_start = today - timedelta(days=29)
    previous_start = today - timedelta(days=59)
    current_rows = list(readings.filter(reading_date__gte=current_start, reading_date__lte=today).values(
        "resource", "quantity", "unit_cost", "currency_code", "recycled_quantity",
    ))
    previous_rows = list(readings.filter(reading_date__gte=previous_start, reading_date__lt=current_start).values(
        "resource", "quantity", "unit_cost", "currency_code", "recycled_quantity",
    ))

    def summarize(rows, resource):
        selected = [row for row in rows if row["resource"] == resource]
        quantity = sum((row["quantity"] for row in selected), 0)
        costs, cost_count = {}, 0
        for row in selected:
            if row["unit_cost"] is not None:
                code = row["currency_code"] or "INR"
                costs[code] = costs.get(code, 0) + row["quantity"] * row["unit_cost"]
                cost_count += 1
        recycled = sum((row["recycled_quantity"] for row in selected), 0)
        return {"quantity": quantity, "costs": costs, "cost_count": cost_count,
                "recycled": recycled, "records": len(selected)}

    energy_readings = EnergyReading.objects.filter(factory__in=factories, reading_at__date__gte=current_start, reading_at__date__lte=today).select_related("factory", "department")
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        energy_readings = energy_readings.filter(department__in=departments)
    energy_rows = list(energy_readings.exclude(consumption_kwh__isnull=True).values("consumption_kwh", "unit_cost", "currency_code", "emission_factor"))
    electricity_costs, electricity_cost_count = {}, 0
    for row in energy_rows:
        if row["unit_cost"] is not None:
            code = row["currency_code"] or "INR"
            electricity_costs[code] = electricity_costs.get(code, 0) + row["consumption_kwh"] * row["unit_cost"]
            electricity_cost_count += 1
    electricity = {
        "quantity": sum((row["consumption_kwh"] or 0 for row in energy_rows), 0),
        "costs": electricity_costs, "cost_count": electricity_cost_count,
        "emissions": sum((row["consumption_kwh"] * row["emission_factor"] for row in energy_rows if row["emission_factor"] is not None), 0),
        "carbon_count": sum(row["emission_factor"] is not None for row in energy_rows),
        "records": len(energy_rows),
    }
    water = summarize(current_rows, SustainabilityReading.Resource.WATER)
    waste = summarize(current_rows, SustainabilityReading.Resource.WASTE)
    previous_waste = summarize(previous_rows, SustainabilityReading.Resource.WASTE)
    waste_reduction = None
    if previous_waste["quantity"] > 0:
        waste_reduction = round((previous_waste["quantity"] - waste["quantity"]) * 100 / previous_waste["quantity"], 1)
    diversion_rate = round(waste["recycled"] * 100 / waste["quantity"], 1) if waste["quantity"] else None

    daily = {}
    for offset in reversed(range(14)):
        day = today - timedelta(days=offset)
        daily[day.isoformat()] = {"label": day.strftime("%d %b"), "electricity": 0, "water": 0, "waste": 0}
    for row in readings.filter(reading_date__gte=today - timedelta(days=13), reading_date__lte=today).values("reading_date", "resource").annotate(total=Sum("quantity")):
        bucket = daily.get(row["reading_date"].isoformat())
        if bucket:
            bucket[row["resource"]] = float(row["total"] or 0)

    for row in energy_readings.filter(reading_at__date__gte=today - timedelta(days=13)).values("reading_at__date").annotate(total=Sum("consumption_kwh")):
        bucket = daily.get(row["reading_at__date"].isoformat())
        if bucket:
            bucket["electricity"] = float(row["total"] or 0)

    latest = readings[:20]
    latest_energy = energy_readings[:20]
    return render(request, "intelligence/sustainability.html", {
        "title": "Energy & Sustainability", "today": today, "current_start": current_start,
        "electricity": electricity, "water": water, "waste": waste,
        "waste_reduction": waste_reduction, "diversion_rate": diversion_rate,
        "daily_json": json.dumps(list(daily.values())), "latest": latest, "latest_energy": latest_energy,
        "can_manage": can_manage_quality(profile),
        "reading_count": readings.count(), "carbon_complete": electricity["carbon_count"] == electricity["records"] and electricity["records"] > 0,
    })


@login_required
def sustainability_reading_create(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile):
        messages.error(request, "You do not have permission to record sustainability data.")
        return redirect("intelligence:sustainability")
    form = SustainabilityReadingForm(request.POST or None, allowed_factories=factories, allowed_departments=departments, initial={"reading_date": timezone.localdate(), "currency_code": "INR"})
    if request.method == "POST" and form.is_valid():
        reading = form.save(commit=False)
        reading.recorded_by = request.user
        reading.save()
        UserActivity.objects.create(user=request.user, action="sustainability_reading_recorded", details={"reading_id": reading.pk, "resource": reading.resource, "quantity": str(reading.quantity)}, ip_address=request.META.get("REMOTE_ADDR"))
        messages.success(request, "Sustainability reading recorded.")
        return redirect("intelligence:sustainability")
    return render(request, "intelligence/form.html", {"form": form, "title": "Record water or waste data", "button": "Save sustainability reading"})


def assistant_answer(question, profile, factories, departments):
    q = question.lower()
    production = ProductionEntry.objects.filter(factory__in=factories)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        production = production.filter(department__in=departments)
    today = timezone.localdate()
    recent_start = today - timedelta(days=6)
    previous_start = today - timedelta(days=13)

    def production_totals(start, end):
        return production.filter(production_date__range=(start, end)).aggregate(
            actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"),
            downtime=Sum("downtime_minutes"), entries=Count("id"),
        )

    def inventory_shortages():
        inventory = InventoryItem.objects.filter(factory__in=factories, is_active=True)
        movement_totals = StockMovement.objects.filter(item__in=inventory).values("item_id").annotate(
            balance=Sum(Case(
                When(kind__in=[StockMovement.Kind.RECEIPT, StockMovement.Kind.ADJUST_IN], then=F("quantity")),
                default=F("quantity") * -1,
                output_field=DecimalField(max_digits=16, decimal_places=3),
            ))
        )
        balances = {row["item_id"]: row["balance"] or 0 for row in movement_totals}
        shortages = []
        for item in inventory.select_related("factory"):
            balance = balances.get(item.pk, 0)
            if item.minimum_stock > 0 and balance <= item.minimum_stock:
                shortages.append((item, balance))
        shortages.sort(key=lambda row: (float(row[0].minimum_stock - row[1]), row[0].name.lower()), reverse=True)
        return shortages

    def stock_summary():
        shortages = inventory_shortages()
        if not shortages:
            return "No active inventory items are at or below their configured minimum stock in your assigned scope. This depends on stock movements and minimum levels being up to date."
        lines = [f"{item.name} ({item.sku}, {item.factory.name}): {balance:g} {item.unit_of_measure} available; minimum is {item.minimum_stock:g}." for item, balance in shortages[:8]]
        extra = f"\nAnd {len(shortages) - 8} more item(s)." if len(shortages) > 8 else ""
        return f"{len(shortages)} inventory item(s) are at or below their minimum stock:\n" + "\n".join(lines) + extra + "\nRecommended action: verify the physical count, then review open purchase documents or create a requisition in Operations → Inventory."

    def production_summary(explain=False):
        current = production_totals(recent_start, today)
        previous = production_totals(previous_start, recent_start - timedelta(days=1))
        actual = current["actual"] or 0
        rejected = current["rejected"] or 0
        downtime = current["downtime"] or 0
        previous_actual = previous["actual"] or 0
        reject_rate = round(rejected * 100 / actual, 1) if actual else 0
        if not current["entries"]:
            return "No production entries are recorded for the last 7 days in your assigned scope, so I can’t identify a production trend yet. Record daily output and downtime in Production to enable a grounded summary."
        change = round((actual - previous_actual) * 100 / previous_actual, 1) if previous_actual else None
        work_orders = WorkOrder.objects.filter(factory__in=factories).exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED])
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
            work_orders = work_orders.filter(department__in=departments)
        open_orders = work_orders.count()
        lines = [f"Last 7 days: {actual:,} units produced, {rejected:,} rejected ({reject_rate}% reject rate), and {downtime:,} recorded downtime minutes."]
        lines.append(f"Open work orders: {open_orders}.")
        if change is not None:
            lines.append(f"Output is {abs(change)}% {'above' if change >= 0 else 'below'} the previous 7 days ({previous_actual:,} units).")
        else:
            lines.append("There is no production output in the previous 7-day period for comparison.")
        actions = []
        if downtime >= 300:
            actions.append("Review the highest-downtime production entries and match them to machine maintenance records.")
        if reject_rate >= 3:
            actions.append("Review rejected output by product and production unit, then verify the latest inspection findings.")
        if change is not None and change < -10:
            actions.append("Compare current work-order schedules and unit downtime against the previous week to locate the main output drop.")
        if not actions:
            actions.append("Keep daily production, reject, and downtime entries current; no large issue is evident from these recorded indicators.")
        if explain:
            lines.append("Possible explanation: recorded downtime, reject rate, and week-over-week output are signals to investigate, not proof of a root cause.")
        lines.append("Recommended next steps: " + " ".join(actions))
        return "\n".join(lines)

    if any(word in q for word in ("shortage", "shortages", "low stock", "inventory", "stock level", "materials")):
        return stock_summary()
    if any(word in q for word in ("billing", "invoice", "revenue", "payment", "receivable", "supplier bill")):
        billing = BillingDocument.objects.filter(factory__in=factories)
        sales = billing.filter(kind=BillingDocument.Kind.SALES_INVOICE)
        purchase_bills = billing.filter(kind=BillingDocument.Kind.SUPPLIER_BILL)
        overdue = sales.filter(due_date__lt=today).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID])
        sales_total = sales.exclude(status=BillingDocument.Status.VOID).aggregate(total=Sum("total"))["total"] or 0
        receivable = sales.filter(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID]).aggregate(total=Sum(F("total") - F("amount_paid"), output_field=DecimalField()))["total"] or 0
        payable = purchase_bills.filter(status__in=[BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID]).aggregate(total=Sum(F("total") - F("amount_paid"), output_field=DecimalField()))["total"] or 0
        return f"Billing in your assigned factories: {sales.count()} sales invoices worth {sales_total:,.2f}; open receivables {receivable:,.2f}; open supplier bills payable {payable:,.2f}; overdue sales invoices {overdue.count()}. These totals follow current document records and exclude cancelled/paid balances where applicable."
    if any(word in q for word in ("recommend", "recommendation", "what should", "what can we do", "action", "actions", "next step")):
        stock = inventory_shortages()
        production_data = production_totals(recent_start, today)
        actions = []
        if stock:
            names = ", ".join(item.name for item, _ in stock[:3])
            actions.append(f"check replenishment for {len(stock)} low-stock item(s), including {names}")
        actual, rejected = production_data["actual"] or 0, production_data["rejected"] or 0
        reject_rate = round(rejected * 100 / actual, 1) if actual else 0
        if (production_data["downtime"] or 0) >= 300:
            actions.append("review production entries with the most downtime and check related maintenance records")
        if reject_rate >= 3:
            actions.append(f"investigate the {reject_rate}% recorded reject rate by product and unit")
        if not actions:
            actions.append("continue tracking daily output, downtime, stock movements, and minimum stock levels; current records show no urgent trigger")
        return "Recommended actions based on records in your assigned scope:\n- " + "\n- ".join(actions) + "\nThese are decision-support suggestions; confirm owners and timing with your operations team."
    if any(word in q for word in ("production", "output", "downtime", "reject", "rejected", "behind", "problem", "problems", "issue", "issues", "explain", "performance")):
        return production_summary(explain=any(word in q for word in ("why", "explain", "problem", "issue")))
    if any(word in q for word in ("summary", "summarize", "overview", "operations", "operation")):
        production_answer = production_summary()
        stock_answer = stock_summary()
        machines = scoped_machines(profile, factories, departments)
        open_maintenance = MaintenanceRecord.objects.filter(machine__in=machines).exclude(status=MaintenanceRecord.Status.COMPLETED).count()
        return f"Operational summary\n{production_answer}\n\n{stock_answer}\n\nOpen maintenance records: {open_maintenance}."
    if any(word in q for word in ("employee", "workforce", "headcount", "staff")):
        qs = Employee.objects.filter(factory__in=factories)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): qs = qs.filter(department__in=departments)
        return f"There are {qs.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count()} active employees in your assigned scope."
    if any(word in q for word in ("factory", "factories")):
        return f"Your account can access {factories.count()} factory/factories."
    if "department" in q:
        return f"Your account can access {departments.count()} department(s)."
    if any(word in q for word in ("energy", "electricity", "power", "kwh")):
        readings = EnergyReading.objects.filter(factory__in=factories, reading_at__date=timezone.localdate())
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): readings = readings.filter(department__in=departments)
        total = readings.aggregate(total=Sum("consumption_kwh"))["total"]
        return f"Recorded electricity consumption today is {total} kWh." if total is not None else "No calculated energy consumption is recorded today in your assigned scope."
    if any(word in q for word in ("quality", "inspection", "defect")):
        qs = QualityInspection.objects.filter(factory__in=factories)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): qs = qs.filter(department__in=departments)
        return f"Quality records in your scope: {qs.count()} inspections, {qs.filter(status=QualityInspection.Status.FAIL).count()} marked failed, and {qs.filter(status=QualityInspection.Status.PENDING).count()} awaiting review. These are human review results; no AI model is connected."
    if any(word in q for word in ("attendance", "present", "absent")):
        qs = Attendance.objects.filter(employee__factory__in=factories, date=timezone.localdate())
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): qs = qs.filter(employee__department__in=departments)
        return f"Today's attendance records: {qs.count()} total, {qs.filter(status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY]).count()} present/late/half-day, and {qs.filter(status=Attendance.Status.ABSENT).count()} absent."
    if any(word in q for word in ("leave", "pending")):
        qs = LeaveRequest.objects.filter(employee__factory__in=factories, status=LeaveRequest.ApprovalStatus.PENDING)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): qs = qs.filter(employee__department__in=departments)
        return f"There are {qs.count()} pending leave requests in your assigned scope."
    return "I can summarize production performance, compare weekly output, identify recorded downtime or reject signals, find items at or below minimum stock, summarize invoice and receivable totals, recommend operational follow-up, and summarize workforce, attendance, leave, energy, quality, factories, or departments in your assigned scope."


@login_required
def assistant_view(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    answer = None
    question = ""
    if request.method == "POST":
        question = request.POST.get("question", "").strip()[:500]
        if question:
            answer = assistant_answer(question, profile, factories, departments)
            AssistantQuery.objects.create(user=request.user, question=question, answer=answer)
            UserActivity.objects.create(user=request.user, action="industry_assistant_query", details={"question": question}, ip_address=request.META.get("REMOTE_ADDR"))
    history = AssistantQuery.objects.filter(user=request.user)[:10]
    return render(request, "intelligence/assistant.html", {"answer": answer, "question": question, "history": history})


def evaluate_workflow_rules(rules=None):
    """Evaluate enabled rules and create one notification per matching record/rule."""
    from apps.operations.models import ProcurementDocument
    from apps.workforce.models import LeaveRequest
    today = timezone.localdate()
    if rules is None: rules = WorkflowRule.objects.filter(is_enabled=True).select_related("factory")
    created = 0
    manager_roles = (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER)
    for rule in rules:
        factories = Factory.objects.filter(pk=rule.factory_id) if rule.factory_id else Factory.objects.none()
        events = []
        if rule.event == WorkflowRule.Event.LOW_STOCK:
            for item in InventoryItem.objects.filter(factory__in=factories, is_active=True).select_related("factory"):
                balance = inventory_balance(item)
                if item.minimum_stock > 0 and balance <= item.minimum_stock:
                    events.append((f"item:{item.pk}", f"Low stock: {item.name}", f"{item.sku} has {balance} {item.unit_of_measure}; minimum is {item.minimum_stock}.", "/operations/inventory/"))
        elif rule.event == WorkflowRule.Event.LATE_DELIVERY:
            for order in ProcurementDocument.objects.filter(factory__in=factories, kind=ProcurementDocument.Kind.ORDER, expected_date__lt=today).exclude(status__in=[ProcurementDocument.Status.RECEIVED, ProcurementDocument.Status.CANCELLED]).select_related("supplier"):
                events.append((f"order:{order.pk}", f"Late delivery: {order.number}", f"Purchase order {order.number} passed its expected date of {order.expected_date}.", "/operations/supply-chain/"))
        elif rule.event == WorkflowRule.Event.MAINTENANCE_DUE:
            for record in MaintenanceRecord.objects.filter(machine__factory__in=factories, scheduled_for__lte=today).exclude(status=MaintenanceRecord.Status.COMPLETED).select_related("machine"):
                events.append((f"maintenance:{record.pk}", f"Maintenance due: {record.machine.asset_tag}", f"{record.summary} was scheduled for {record.scheduled_for}.", "/intelligence/machines/predictive/"))
        elif rule.event == WorkflowRule.Event.QUALITY_REVIEW:
            for inspection in QualityInspection.objects.filter(factory__in=factories, status=QualityInspection.Status.PENDING):
                events.append((f"inspection:{inspection.pk}", f"Inspection review: {inspection.product_reference}", f"Quality inspection {inspection.product_reference} is awaiting review.", "/intelligence/quality/history/?status=pending"))
        elif rule.event == WorkflowRule.Event.LEAVE_APPROVAL:
            for leave in LeaveRequest.objects.filter(employee__factory__in=factories, status=LeaveRequest.ApprovalStatus.PENDING).select_related("employee"):
                events.append((f"leave:{leave.pk}", f"Leave approval: {leave.employee.employee_id}", f"{leave.employee.first_name} {leave.employee.last_name} has a leave request awaiting review.", "/workforce/leave/"))
        active_fingerprints = {event[0] for event in events}
        for fingerprint, title, message, target_url in events:
            alert, is_new = WorkflowAlert.objects.get_or_create(rule=rule, fingerprint=fingerprint, defaults={"message": message, "target_url": target_url})
            if not is_new: continue
            factory = rule.factory
            recipients = UserProfile.objects.filter(role__in=manager_roles, user__is_active=True).select_related("user", "company", "factory")
            if factory:
                recipients = recipients.filter(Q(role=UserProfile.Role.SUPER_ADMIN) | Q(role=UserProfile.Role.COMPANY_ADMIN, company_id=factory.company_id) | Q(factory_id=factory.pk))
            for recipient in recipients:
                Notification.objects.create(user=recipient.user, title=title, message=message, target_url=target_url)
            created += 1
        escalation_cutoff = timezone.now() - timedelta(hours=rule.escalation_after_hours)
        stale_alerts = rule.alerts.filter(fingerprint__in=active_fingerprints, escalated_at__isnull=True, created_at__lte=escalation_cutoff)
        for alert in stale_alerts:
            factory = rule.factory
            escalations = UserProfile.objects.filter(role__in=(UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN), user__is_active=True).select_related("user")
            if factory: escalations = escalations.filter(Q(role=UserProfile.Role.SUPER_ADMIN) | Q(company_id=factory.company_id))
            for recipient in escalations:
                Notification.objects.get_or_create(user=recipient.user, title=f"Escalated: {rule.name}", message=alert.message, target_url=alert.target_url)
            alert.escalated_at = timezone.now(); alert.save(update_fields=["escalated_at"])
        rule.last_run_at = timezone.now()
        rule.save(update_fields=["last_run_at"])
    return created


@login_required
def workflow_rules(request):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile): raise Http404
    form = WorkflowRuleForm(request.POST if request.method == "POST" and request.POST.get("action") == "create" else None, factories=factories)
    if request.method == "POST" and request.POST.get("action") == "create" and form.is_valid():
        rule = form.save(commit=False); rule.created_by = request.user; rule.save()
        messages.success(request, "Automation rule saved.")
        return redirect("intelligence:workflow_rules")
    rules = WorkflowRule.objects.filter(factory__in=factories).select_related("factory", "created_by")
    return render(request, "intelligence/workflow_rules.html", {"form": form, "rules": rules, "can_manage": True})


@login_required
@require_POST
def workflow_run(request, pk=None):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile): raise Http404
    rules = WorkflowRule.objects.filter(factory__in=factories, is_enabled=True)
    if pk is not None: rules = rules.filter(pk=pk)
    count = evaluate_workflow_rules(rules)
    messages.success(request, f"Workflow run complete. {count} new alert(s) generated.")
    return redirect("intelligence:workflow_rules")


@login_required
@require_POST
def workflow_toggle(request, pk):
    profile, companies, factories, departments = workspace_scope(request.user)
    if not can_manage_quality(profile): raise Http404
    rule = WorkflowRule.objects.filter(factory__in=factories, pk=pk).first()
    if rule and request.method == "POST":
        rule.is_enabled = not rule.is_enabled
        rule.save(update_fields=["is_enabled"])
        messages.success(request, f"Automation rule {'enabled' if rule.is_enabled else 'paused'}.")
    return redirect("intelligence:workflow_rules")
