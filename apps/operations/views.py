import csv
import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Count, Max, F, ExpressionWrapper, IntegerField, Case, When, Value, DecimalField
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import UserActivity, UserProfile
from apps.organization.models import Company, Factory, Department, ProductionUnit
from .forms import BillingDocumentForm, BillingLineForm, BillingPaymentForm, BillingRefundForm, CustomerForm, InventoryBatchForm, InventoryItemForm, ProcurementDocumentForm, ProductCategoryForm, ProductForm, ProductMaterialRequirementForm, ProductionEntryForm, ProductionPlanForm, ProductionPlanSelectionForm, RawMaterialPurchaseOrderForm, ShipmentTrackingForm, StockMovementForm, StockTransferForm, SupplierEvaluationForm, SupplierForm, WarehouseBinForm, WarehouseForm, WorkOrderForm
from .models import BillingDocument, BillingLine, BillingPayment, BillingRefund, Customer, InventoryBatch, InventoryItem, ProcurementDocument, Product, ProductCategory, ProductMaterialRequirement, ProductionEntry, ProductionPlan, StockMovement, StockTransfer, Supplier, SupplierEvaluation, Warehouse, WarehouseBin, WorkOrder

MANAGERS = (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN, UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER)

def scope(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE})
    if user.is_superuser and profile.role != UserProfile.Role.SUPER_ADMIN:
        profile.role = UserProfile.Role.SUPER_ADMIN; profile.save(update_fields=["role", "updated_at"])
    companies, factories, departments, units = Company.objects.all(), Factory.objects.all(), Department.objects.all(), ProductionUnit.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies); departments = departments.filter(factory__in=factories); units = units.filter(department__in=departments)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        if profile.factory_id:
            factories = factories.filter(pk=profile.factory_id)
            departments = departments.filter(factory__in=factories)
            units = units.filter(department__in=departments)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE) and profile.department_id:
            departments = departments.filter(pk=profile.department_id)
            units = units.filter(department__in=departments)
    return profile, companies, factories, departments, units

def scoped(queryset, profile, factories, departments, units, factory_field="factory", department_field="department", unit_field="production_unit"):
    if profile.role == UserProfile.Role.COMPANY_ADMIN: return queryset.filter(**{f"{factory_field}__in": factories})
    if profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        queryset = queryset.filter(**{f"{factory_field}__in": factories})
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE) and profile.department_id: queryset = queryset.filter(**{f"{department_field}__in": departments})
    return queryset

def manager(profile): return profile.role in MANAGERS

def billing_manager(user, profile): return manager(profile) or user.has_perm("billing.manage_billing") or user.has_perm("operations.manage_billing")

def log(request, action, obj, details=None):
    activity_details = {"record_id": obj.pk, "record": str(obj)}
    if details:
        activity_details.update(details)
    UserActivity.objects.create(user=request.user, action=action, details=activity_details, ip_address=request.META.get("REMOTE_ADDR"))

def log_billing_transaction(request, action, record, document, reason=""):
    details = {
        "record_id": record.pk,
        "invoice_number": document.number,
        "amount": str(record.amount),
        "method": record.get_method_display(),
        "reference": record.reference,
    }
    if reason:
        details["reason"] = reason
    UserActivity.objects.create(
        user=request.user,
        action=action,
        details=details,
        ip_address=request.META.get("REMOTE_ADDR"),
    )

def render_page(request, template, context): return render(request, template, context)


def inventory_balance(item, warehouse=None, bin_location=None, batch=None):
    movements = StockMovement.objects.filter(item=item)
    if warehouse is not None: movements = movements.filter(warehouse=warehouse)
    if bin_location is not None: movements = movements.filter(bin_location=bin_location)
    if batch is not None: movements = movements.filter(batch=batch)
    balance = movements.aggregate(total=Sum(Case(
        When(kind__in=[StockMovement.Kind.RECEIPT, StockMovement.Kind.ADJUST_IN], then=F("quantity")),
        default=F("quantity") * -1,
        output_field=DecimalField(max_digits=16, decimal_places=3),
    ))) ["total"]
    return balance or 0


@login_required
def inventory(request):
    profile, companies, factories, departments, units = scope(request.user)
    items = InventoryItem.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    warehouse_rows = Warehouse.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    suppliers = Supplier.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    documents = ProcurementDocument.objects.filter(factory__in=factories).select_related("item", "supplier", "warehouse", "factory", "created_by")
    movements = StockMovement.objects.filter(item__factory__in=factories).select_related("item", "warehouse", "created_by", "procurement_document")
    item_rows, stock_value, low_stock = [], 0, 0
    cutoff = timezone.localdate() - timedelta(days=30)
    for item in items:
        balance = inventory_balance(item)
        value = balance * item.standard_cost
        consumption = StockMovement.objects.filter(item=item, kind=StockMovement.Kind.CONSUMPTION, created_at__date__gte=cutoff).aggregate(total=Sum("quantity"))["total"] or 0
        days_remaining = round(float(balance / (consumption / 30)), 1) if consumption and balance > 0 else None
        is_low = item.minimum_stock > 0 and balance <= item.minimum_stock
        low_stock += int(is_low)
        stock_value += value
        item_rows.append({"item": item, "balance": balance, "value": value, "is_low": is_low, "days_remaining": days_remaining})
    item_rows.sort(key=lambda row: (not row["is_low"], row["item"].name.lower()))
    return render_page(request, "operations/inventory.html", {
        "item_rows": item_rows, "warehouses": warehouse_rows, "suppliers": suppliers,
        "documents": documents[:20], "movements": movements[:25], "low_stock": low_stock,
        "stock_value": stock_value, "can_manage": manager(profile),
    })


@login_required
def material_planning(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile):
        raise Http404

    plans = ProductionPlan.objects.filter(factory__in=factories).exclude(
        status__in=[ProductionPlan.Status.COMPLETED, ProductionPlan.Status.CANCELLED]
    ).select_related("factory", "product").order_by("-start_date", "name")
    if profile.role == UserProfile.Role.DEPARTMENT_MANAGER:
        plans = plans.filter(department__in=departments)
    selected_plan = None
    selected_plan_value = request.GET.get("production_plan") or request.POST.get("production_plan")
    plan_form = ProductionPlanSelectionForm(
        {"production_plan": selected_plan_value} if selected_plan_value else None,
        plans=plans,
    )
    if selected_plan_value:
        if plan_form.is_valid():
            selected_plan = plan_form.cleaned_data["production_plan"]
    elif plans.exists():
        selected_plan = plans.first()
        plan_form = ProductionPlanSelectionForm(
            initial={"production_plan": selected_plan.pk},
            plans=plans,
        )

    if request.method == "POST" and request.POST.get("action") == "save_requirement":
        existing_requirement = ProductMaterialRequirement.objects.filter(
            factory__in=factories,
            factory_id=request.POST.get("factory"),
            product_id=request.POST.get("product"),
            raw_material_id=request.POST.get("raw_material"),
        ).first()
        requirement_form = ProductMaterialRequirementForm(
            request.POST,
            factories=factories,
            instance=existing_requirement,
        )
        if requirement_form.is_valid():
            requirement = requirement_form.save(commit=False)
            if existing_requirement:
                requirement.save(update_fields=["quantity_per_unit", "updated_at"])
                messages.success(request, "Material requirement updated.")
            else:
                requirement.save()
                messages.success(request, "Raw material added to the product recipe.")
            log(
                request,
                "product_material_requirement_saved",
                requirement,
                details={
                    "factory_id": requirement.factory_id,
                    "product_id": requirement.product_id,
                    "raw_material_id": requirement.raw_material_id,
                    "quantity_per_unit": str(requirement.quantity_per_unit),
                },
            )
            return redirect(f"{request.path}?production_plan={request.POST.get('production_plan', '')}")
        messages.error(request, "Check the product and raw-material requirement details.")
    else:
        requirement_form = ProductMaterialRequirementForm(factories=factories)

    requirement_delete_id = request.POST.get("requirement_id")
    if request.method == "POST" and request.POST.get("action") == "delete_requirement":
        requirement = get_object_or_404(
            ProductMaterialRequirement.objects.filter(factory__in=factories),
            pk=requirement_delete_id,
        )
        requirement_label = str(requirement)
        log(request, "product_material_requirement_deleted", requirement)
        requirement.delete()
        messages.success(request, f"Removed {requirement_label} from the product recipe.")
        return redirect(f"{request.path}?production_plan={request.POST.get('production_plan', '')}")

    material_rows = []
    if selected_plan:
        requirements = ProductMaterialRequirement.objects.filter(
            factory=selected_plan.factory,
            product=selected_plan.product,
        ).select_related("raw_material", "factory", "product")
        produced_quantity = ProductionEntry.objects.filter(
            work_order__plan=selected_plan,
        ).aggregate(total=Sum("actual_quantity"))["total"] or 0
        remaining_quantity = max(selected_plan.target_quantity - produced_quantity, 0)
        for requirement in requirements:
            required_quantity = (
                requirement.quantity_per_unit * remaining_quantity
            ).quantize(Decimal("0.001"))
            on_hand = Decimal(str(inventory_balance(requirement.raw_material)))
            open_orders = ProcurementDocument.objects.filter(
                production_plan=selected_plan,
                item=requirement.raw_material,
                kind=ProcurementDocument.Kind.ORDER,
                status__in=[
                    ProcurementDocument.Status.ORDERED,
                    ProcurementDocument.Status.PART_RECEIVED,
                ],
            ).prefetch_related("receipts")
            incoming = Decimal("0")
            for purchase_order in open_orders:
                received = sum(
                    (
                        movement.quantity
                        for movement in purchase_order.receipts.all()
                        if movement.kind == StockMovement.Kind.RECEIPT
                    ),
                    Decimal("0"),
                )
                incoming += max(purchase_order.quantity - received, Decimal("0"))
            shortage = max(required_quantity - on_hand - incoming, Decimal("0")).quantize(Decimal("0.001"))
            default_warehouse = Warehouse.objects.filter(
                factory=selected_plan.factory,
                is_active=True,
            ).first()
            order_form = RawMaterialPurchaseOrderForm(
                factory=selected_plan.factory,
                initial={"quantity": shortage, "warehouse": default_warehouse},
            )
            material_rows.append({
                "requirement": requirement,
                "required": required_quantity,
                "on_hand": on_hand,
                "incoming": incoming,
                "shortage": shortage,
                "order_form": order_form,
            })

    recipe_requirements = ProductMaterialRequirement.objects.filter(
        factory__in=factories,
    ).select_related("factory", "product", "raw_material")
    selected_orders = (
        ProcurementDocument.objects.filter(
            production_plan=selected_plan,
            kind=ProcurementDocument.Kind.ORDER,
        ).select_related("item", "supplier", "warehouse")
        if selected_plan
        else ProcurementDocument.objects.none()
    )
    factory_rows = [
        {
            "factory": factory,
            "products": Product.objects.filter(
                material_requirements__factory=factory,
            ).distinct().order_by("name"),
        }
        for factory in factories
    ]
    return render_page(request, "operations/material_planning.html", {
        "plans": plans,
        "plan_form": plan_form,
        "selected_plan": selected_plan,
        "requirement_form": requirement_form,
        "material_rows": material_rows,
        "recipe_requirements": recipe_requirements,
        "selected_orders": selected_orders,
        "factory_rows": factory_rows,
        "factories": factories,
        "can_manage": True,
        "title": "Raw material planning",
    })


@login_required
@require_POST
def raw_material_purchase_order(request, plan_id, requirement_id):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile):
        raise Http404
    plan = get_object_or_404(
        ProductionPlan.objects.select_related("factory", "product").filter(
            factory__in=factories,
            **(
                {"department__in": departments}
                if profile.role == UserProfile.Role.DEPARTMENT_MANAGER
                else {}
            ),
        ),
        pk=plan_id,
    )
    requirement = get_object_or_404(
        ProductMaterialRequirement.objects.select_related("raw_material").filter(
            factory=plan.factory,
            product=plan.product,
        ),
        pk=requirement_id,
    )
    form = RawMaterialPurchaseOrderForm(request.POST, factory=plan.factory)
    if form.is_valid():
        with transaction.atomic():
            factory = Factory.objects.select_for_update().get(pk=plan.factory_id)
            today = timezone.localdate()
            prefix = f"PO-{factory.factory_code}-{today:%Y%m%d}-"
            last_number = ProcurementDocument.objects.filter(
                factory=factory,
                number__startswith=prefix,
            ).order_by("-number").values_list("number", flat=True).first()
            sequence = int(last_number.rsplit("-", 1)[-1]) + 1 if last_number else 1
            order = ProcurementDocument.objects.create(
                factory=factory,
                number=f"{prefix}{sequence:04d}",
                kind=ProcurementDocument.Kind.ORDER,
                status=ProcurementDocument.Status.ORDERED,
                item=requirement.raw_material,
                warehouse=form.cleaned_data["warehouse"],
                supplier=form.cleaned_data["supplier"],
                production_plan=plan,
                quantity=form.cleaned_data["quantity"],
                expected_date=form.cleaned_data["expected_date"],
                created_by=request.user,
                notes=form.cleaned_data["notes"] or (
                    f"Raw material purchase for production plan {plan.name} ({plan.product.sku})."
                ),
            )
            log(
                request,
                "raw_material_purchase_order_created",
                order,
                details={
                    "production_plan_id": plan.pk,
                    "product_id": plan.product_id,
                    "raw_material_id": requirement.raw_material_id,
                    "supplier_id": order.supplier_id,
                    "quantity": str(order.quantity),
                },
            )
        messages.success(request, f"Purchase order {order.number} created for {requirement.raw_material.name}.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect(f"{reverse('operations:material_planning')}?production_plan={plan.pk}")


@login_required
def smart_warehouse(request):
    profile, companies, factories, departments, units = scope(request.user)
    items = InventoryItem.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    warehouses = Warehouse.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    code = request.GET.get("code", "").strip()
    match = items.filter(Q(sku__iexact=code) | Q(tracking_code__iexact=code)).first() if code else None
    rows = []
    for item in items:
        for warehouse in warehouses.filter(factory_id=item.factory_id):
            balance = inventory_balance(item, warehouse)
            if balance:
                rows.append({"item": item, "warehouse": warehouse, "balance": balance})
    bin_rows = []
    for bin_location in WarehouseBin.objects.filter(warehouse__in=warehouses, is_active=True).select_related("warehouse"):
        for item in items.filter(factory_id=bin_location.warehouse.factory_id):
            balance = inventory_balance(item, bin_location.warehouse, bin_location=bin_location)
            if balance: bin_rows.append({"bin": bin_location, "item": item, "balance": balance})
    return render_page(request, "operations/smart_warehouse.html", {
        "rows": rows, "warehouses": warehouses, "item_count": items.count(),
        "bin_rows": bin_rows, "code": code, "match": match, "can_manage": manager(profile),
    })


@login_required
def warehouse_control(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    bin_form = WarehouseBinForm(request.POST if request.POST.get("action") == "bin" else None, factories=factories)
    transfer_form = StockTransferForm(request.POST if request.POST.get("action") == "transfer" else None, factories=factories)
    batch_form = InventoryBatchForm(request.POST if request.POST.get("action") == "batch" else None, factories=factories)
    if request.method == "POST":
        action = request.POST.get("action")
        selected = {"bin": bin_form, "transfer": transfer_form, "batch": batch_form}.get(action)
        if selected and selected.is_valid():
            if action == "bin":
                selected.save(); messages.success(request, "Warehouse bin saved.")
            elif action == "batch":
                selected.save(); messages.success(request, "Inventory batch saved.")
            else:
                with transaction.atomic():
                    transfer = selected.save(commit=False)
                    source_item = InventoryItem.objects.select_for_update().get(pk=transfer.item_id, factory__in=factories)
                    available = inventory_balance(source_item, transfer.from_warehouse)
                    if transfer.quantity > available:
                        selected.add_error("quantity", f"Only {available} {source_item.unit_of_measure} are available at the source.")
                    else:
                        transfer.transferred_by = request.user; transfer.save()
                        StockMovement.objects.create(item=source_item, warehouse=transfer.from_warehouse, kind=StockMovement.Kind.ADJUST_OUT, quantity=transfer.quantity, reference=f"Transfer {transfer.pk}", created_by=request.user, notes=f"To {transfer.to_warehouse.code}. {transfer.notes}".strip())
                        StockMovement.objects.create(item=source_item, warehouse=transfer.to_warehouse, kind=StockMovement.Kind.ADJUST_IN, quantity=transfer.quantity, reference=f"Transfer {transfer.pk}", created_by=request.user, notes=f"From {transfer.from_warehouse.code}. {transfer.notes}".strip())
                        log(request, "stock_transfer_completed", transfer, {"from": transfer.from_warehouse.code, "to": transfer.to_warehouse.code, "quantity": str(transfer.quantity)})
                        messages.success(request, "Stock transfer completed and both warehouse movements recorded.")
            if not selected.errors: return redirect("operations:warehouse_control")
    bins = WarehouseBin.objects.filter(warehouse__factory__in=factories).select_related("warehouse", "warehouse__factory")
    batches = list(InventoryBatch.objects.filter(item__factory__in=factories).select_related("item", "warehouse"))
    for batch in batches: batch.on_hand = inventory_balance(batch.item, batch.warehouse, batch=batch)
    transfers = StockTransfer.objects.filter(item__factory__in=factories).select_related("item", "from_warehouse", "to_warehouse", "transferred_by")[:50]
    return render_page(request, "operations/warehouse_control.html", {"bin_form": bin_form, "transfer_form": transfer_form, "batch_form": batch_form, "bins": bins, "batches": batches, "transfers": transfers})


@login_required
def supply_chain(request):
    profile, companies, factories, departments, units = scope(request.user)
    today = timezone.localdate()
    cutoff = today - timedelta(days=30)
    items = InventoryItem.objects.filter(factory__in=factories, is_active=True).select_related("factory")
    documents = ProcurementDocument.objects.filter(factory__in=factories).select_related("supplier", "item", "warehouse")
    rows = []
    for item in items:
        balance = inventory_balance(item)
        used = StockMovement.objects.filter(item=item, kind=StockMovement.Kind.CONSUMPTION, created_at__date__gte=cutoff).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        daily_rate = used / Decimal("30")
        days_cover = round(float(balance / daily_rate), 1) if daily_rate > 0 and balance > 0 else None
        related_orders = documents.filter(item=item, kind=ProcurementDocument.Kind.ORDER).exclude(status=ProcurementDocument.Status.CANCELLED)
        overdue = related_orders.filter(expected_date__lt=today).exclude(status=ProcurementDocument.Status.RECEIVED).count()
        rows.append({"item": item, "balance": balance, "used_30d": used, "days_cover": days_cover, "low": bool(item.minimum_stock and balance <= item.minimum_stock), "overdue": overdue})
    rows.sort(key=lambda row: (row["days_cover"] is None, row["days_cover"] if row["days_cover"] is not None else 999999))
    suppliers = Supplier.objects.filter(factory__in=factories, is_active=True)
    supplier_rows = []
    evaluation_form = SupplierEvaluationForm(request.POST if request.method == "POST" else None, factories=factories)
    if request.method == "POST" and evaluation_form.is_valid():
        evaluation = evaluation_form.save(commit=False); evaluation.evaluated_by = request.user; evaluation.save()
        log(request, "supplier_evaluated", evaluation)
        messages.success(request, "Supplier evaluation saved.")
        return redirect("operations:supply_chain")
    for supplier in suppliers:
        orders = documents.filter(supplier=supplier, kind=ProcurementDocument.Kind.ORDER)
        open_overdue = orders.filter(expected_date__lt=today).exclude(status__in=[ProcurementDocument.Status.RECEIVED, ProcurementDocument.Status.CANCELLED]).count()
        received = orders.filter(status=ProcurementDocument.Status.RECEIVED, expected_date__isnull=False)
        on_time = 0
        for order in received:
            final_receipt = order.receipts.filter(kind=StockMovement.Kind.RECEIPT).aggregate(last_received=Max("created_at"))["last_received"]
            if final_receipt and timezone.localtime(final_receipt).date() <= order.expected_date: on_time += 1
        completed_count = received.count()
        supplier_rows.append({"supplier": supplier, "orders": orders.count(), "open_overdue": open_overdue, "on_time_pct": round(100 * on_time / completed_count) if completed_count else None, "evaluation_score": supplier.evaluation_score, "risk": "High" if open_overdue else ("On-time" if completed_count else "Unrated")})
    return render_page(request, "operations/supply_chain.html", {
        "rows": rows, "supplier_rows": supplier_rows, "documents": documents.order_by("expected_date")[:20],
        "overdue_count": documents.filter(expected_date__lt=today).exclude(status__in=[ProcurementDocument.Status.RECEIVED, ProcurementDocument.Status.CANCELLED]).count(),
        "can_manage": manager(profile), "cutoff": cutoff, "evaluation_form": evaluation_form,
    })


@login_required
def shipment_update(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    document = get_object_or_404(ProcurementDocument, pk=pk, factory__in=factories, kind=ProcurementDocument.Kind.ORDER)
    form = ShipmentTrackingForm(request.POST or None, instance=document)
    if request.method == "POST" and form.is_valid():
        form.save(); log(request, "shipment_tracking_updated", document); messages.success(request, "Shipment tracking details updated.")
        return redirect("operations:supply_chain")
    return render_page(request, "operations/form.html", {"form": form, "title": f"Track shipment {document.number}"})


@login_required
def billing_home(request):
    profile, companies, factories, departments, units = scope(request.user)
    docs = BillingDocument.objects.filter(factory__in=factories).select_related("factory", "customer", "supplier", "procurement_document")
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        customers = Customer.objects.filter(company__in=companies)
    else:
        customers = Customer.objects.filter(company__factories__in=factories).distinct()
    customer_query = request.GET.get("customer", "").strip()
    if customer_query: customers = customers.filter(Q(customer_id__icontains=customer_query) | Q(name__icontains=customer_query) | Q(tax_registration_number__icontains=customer_query))
    if request.GET.get("kind") in BillingDocument.Kind.values:
        docs = docs.filter(kind=request.GET["kind"])
    today = timezone.localdate()
    status_filter = request.GET.get("status", "")
    if status_filter == BillingDocument.PaymentStatus.OVERDUE:
        docs = docs.filter(
            Q(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])
            | Q(kind=BillingDocument.Kind.SUPPLIER_BILL, status=BillingDocument.Status.APPROVED),
            due_date__lt=today,
            amount_paid__lt=F("total"),
        )
    elif status_filter == BillingDocument.PaymentStatus.UNPAID:
        docs = docs.filter(
            Q(status=BillingDocument.Status.ISSUED)
            | Q(kind=BillingDocument.Kind.SUPPLIER_BILL, status=BillingDocument.Status.APPROVED),
            amount_paid=0,
        ).filter(Q(due_date__isnull=True) | Q(due_date__gte=today))
    elif status_filter == BillingDocument.PaymentStatus.PART_PAID:
        docs = docs.filter(
            status=BillingDocument.Status.PART_PAID,
        ).filter(Q(due_date__isnull=True) | Q(due_date__gte=today))
    elif status_filter in BillingDocument.Status.values:
        docs = docs.filter(status=status_filter)
    active_docs = docs.exclude(status=BillingDocument.Status.VOID)
    sales = active_docs.filter(kind=BillingDocument.Kind.SALES_INVOICE)
    payables = active_docs.filter(kind=BillingDocument.Kind.SUPPLIER_BILL)
    payable_obligations = payables.filter(
        status__in=[
            BillingDocument.Status.APPROVED,
            BillingDocument.Status.ISSUED,
            BillingDocument.Status.PART_PAID,
        ]
    )
    sales_due = sum((document.balance_due for document in sales.filter(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])), Decimal("0"))
    supplier_due = sum(
        (document.balance_due for document in payable_obligations),
        Decimal("0"),
    )
    suppliers = Supplier.objects.filter(factory__in=factories, is_active=True).select_related(
        "factory"
    )
    supplier_query = request.GET.get("supplier", "").strip()
    if supplier_query:
        suppliers = suppliers.filter(
            Q(name__icontains=supplier_query)
            | Q(gstin__icontains=supplier_query)
            | Q(state__icontains=supplier_query)
        )
    document_rows = list(docs[:100])
    if status_filter in (
        BillingDocument.PaymentStatus.OVERDUE,
        BillingDocument.PaymentStatus.UNPAID,
        BillingDocument.PaymentStatus.PART_PAID,
        BillingDocument.PaymentStatus.PAID,
    ):
        document_rows = [
            document
            for document in document_rows
            if document.payment_status == status_filter
        ]
    context = {
        "documents": document_rows, "customers": customers, "suppliers": suppliers[:100],
        "can_manage": billing_manager(request.user, profile), "customer_query": customer_query,
        "supplier_query": supplier_query,
        "sales_total": sales.filter(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID, BillingDocument.Status.PAID]).aggregate(total=Sum("total"))["total"] or 0,
        "sales_due": sales_due,
        "bills_total": payables.aggregate(total=Sum("total"))["total"] or 0,
        "supplier_due": supplier_due,
        "overdue_count": sum(
            document.payment_status == BillingDocument.PaymentStatus.OVERDUE
            for document in active_docs.filter(
            Q(status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])
            | Q(kind=BillingDocument.Kind.SUPPLIER_BILL, status=BillingDocument.Status.APPROVED),
            due_date__lt=today,
            ).iterator()
        ),
        "supplier_overdue_count": sum(
            document.payment_status == BillingDocument.PaymentStatus.OVERDUE
            for document in payable_obligations.filter(due_date__lt=today).iterator()
        ),
        "kind_filter": request.GET.get("kind", ""), "status_filter": status_filter,
    }
    return render_page(request, "operations/billing.html", context)


@login_required
def customer_create(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not billing_manager(request.user, profile): raise Http404
    company_ids = factories.values_list("company_id", flat=True).distinct()
    company_queryset = companies.filter(pk__in=company_ids)
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save(commit=False)
        customer.created_by = request.user
        company_id = request.POST.get("company")
        customer.company = company_queryset.filter(pk=company_id).first()
        if customer.company:
            customer.save()
            log(
                request,
                "billing_customer_created",
                customer,
                details={
                    "customer_id": customer.customer_id,
                    "customer_type": customer.customer_type,
                    "gstin": customer.tax_registration_number,
                    "state": customer.state,
                    "state_code": customer.state_code,
                    "official_records_confirmed": True,
                },
            )
            messages.success(request, "Customer saved.")
            return redirect("operations:billing")
        form.add_error(None, "Choose a company in your workspace.")
    return render_page(request, "operations/customer_form.html", {"form": form, "companies": company_queryset})


@login_required
def customer_detail(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    company_ids = factories.values_list("company_id", flat=True).distinct()
    customer = get_object_or_404(Customer.objects.filter(company_id__in=company_ids), pk=pk)
    invoices = customer.invoices.filter(factory__in=factories, kind=BillingDocument.Kind.SALES_INVOICE).select_related("factory").prefetch_related("lines", "payments")
    outstanding_by_currency = invoices.filter(
        status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID]
    ).values("currency_code").annotate(
        total=Sum(F("total") - F("amount_paid"))
    ).order_by("currency_code")
    return render_page(request, "operations/customer_detail.html", {
        "customer": customer,
        "invoices": invoices,
        "outstanding_by_currency": outstanding_by_currency,
        "can_manage": billing_manager(request.user, profile),
    })


@login_required
def billing_create(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not billing_manager(request.user, profile): raise Http404
    form = BillingDocumentForm(request.POST or None, factories=factories)
    line_forms = []
    if request.method == "POST" and form.is_valid():
        try:
            import json
            payload = json.loads(request.POST.get("lines", "[]"))
            if not isinstance(payload, list) or not payload: raise ValueError
        except (ValueError, TypeError):
            payload = []
            form.add_error(None, "Add at least one invoice or bill line.")
        if payload and form.is_valid():
            validated_lines = []
            for line_data in payload:
                line_form = BillingLineForm(line_data)
                if line_form.is_valid(): validated_lines.append(line_form.cleaned_data)
                else:
                    line_forms.append(line_form)
                    form.add_error(None, "Check the line item details.")
            subtotal = sum((data["quantity"] * data["unit_price"] for data in validated_lines), Decimal("0")).quantize(Decimal("0.01"))
            if len(validated_lines) == len(payload) and form.cleaned_data["discount_amount"] > subtotal:
                form.add_error("discount_amount", "Discount cannot exceed the subtotal.")
            elif len(validated_lines) == len(payload):
                with transaction.atomic():
                    doc = form.save(commit=False)
                    doc.created_by = request.user
                    if doc.kind == BillingDocument.Kind.SALES_INVOICE:
                        doc.status = BillingDocument.Status.DRAFT
                    factory = factories.select_for_update().get(pk=doc.factory_id)
                    doc.factory = factory
                    if not doc.number:
                        doc.number = BillingDocument.next_number(doc.factory, doc.kind, doc.issue_date)
                    if doc.kind == BillingDocument.Kind.SALES_INVOICE:
                        from apps.billing.tax import calculate_gst_lines

                        for data, raw_data in zip(validated_lines, payload):
                            if not data["product"] and "tax_rate" not in raw_data:
                                data["tax_rate"] = doc.tax_rate
                        try:
                            gst = calculate_gst_lines(validated_lines, doc.discount_amount, doc.issue_date, doc)
                        except ValidationError as error:
                            form.add_error(None, error)
                            gst = None
                        if gst is not None:
                            for field, value in gst.items():
                                if field not in {"lines", "taxable_value"}:
                                    setattr(doc, field, value)
                    else:
                        doc.subtotal = subtotal
                        doc.tax_amount = ((subtotal - doc.discount_amount) * doc.tax_rate / Decimal("100")).quantize(Decimal("0.01"))
                        doc.total = subtotal - doc.discount_amount + doc.tax_amount
                        gst = None
                    if gst is not None or doc.kind != BillingDocument.Kind.SALES_INVOICE:
                        doc.save()
                        line_data_to_save = gst["lines"] if gst is not None else validated_lines
                        for data in line_data_to_save:
                            BillingLine.objects.create(
                                document=doc,
                                description=data["description"],
                                product=data["product"],
                                quantity=data["quantity"],
                                unit_price=data["unit_price"],
                                line_total=data.get("line_total", (data["quantity"] * data["unit_price"]).quantize(Decimal("0.01"))),
                                **({
                                    "discount_amount": data["discount_amount"],
                                    "taxable_value": data["taxable_value"],
                                    "tax_rate": data["tax_rate"],
                                    "hsn_sac_code": data["hsn_sac_code"],
                                    "cgst_amount": data["cgst_amount"],
                                    "sgst_amount": data["sgst_amount"],
                                    "igst_amount": data["igst_amount"],
                                } if gst is not None else {}),
                            )
                        log(request, "billing_document_created", doc)
                if gst is not None or doc.kind != BillingDocument.Kind.SALES_INVOICE:
                    messages.success(request, f"{doc.get_kind_display()} {doc.number} created.")
                    return redirect("operations:billing")
    return render_page(request, "operations/billing_form.html", {"form": form, "line_forms": line_forms, "products": Product.objects.filter(is_active=True), "default_currency": "INR"})


@login_required
def billing_detail(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    doc = get_object_or_404(
        BillingDocument.objects.filter(factory__in=factories)
        .select_related("factory", "customer", "supplier", "procurement_document")
        .prefetch_related("payments__refunds", "payments__recorded_by"),
        pk=pk,
    )
    action = request.POST.get("action", "record_payment") if request.method == "POST" else ""
    payment_form = BillingPaymentForm(request.POST if action == "record_payment" else None)
    refund_form = BillingRefundForm(
        request.POST if action == "record_refund" else None,
        invoice=doc,
    )
    if request.method == "POST":
        if not billing_manager(request.user, profile): raise Http404
        if action == "record_payment":
            allowed_statuses = (
                (BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID)
                if doc.kind == BillingDocument.Kind.SALES_INVOICE
                else (BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID)
            )
            if doc.status not in allowed_statuses:
                messages.error(request, "This document is not verified or eligible for payment.")
            elif payment_form.is_valid():
                with transaction.atomic():
                    locked = BillingDocument.objects.select_for_update().get(pk=doc.pk)
                    payment = payment_form.save(commit=False)
                    if locked.status not in allowed_statuses:
                        messages.error(request, "This document is no longer eligible for payment.")
                    elif payment.amount > locked.balance_due:
                        payment_form.add_error("amount", f"Payment cannot exceed the remaining balance of {locked.balance_due}.")
                    elif payment.amount <= 0:
                        payment_form.add_error("amount", "This document has no balance due.")
                    else:
                        payment.document = locked
                        payment.recorded_by = request.user
                        payment.save()
                        locked.amount_paid += payment.amount
                        locked.status = BillingDocument.Status.PAID if locked.amount_paid >= locked.total else BillingDocument.Status.PART_PAID
                        locked.save(update_fields=["amount_paid", "status"])
                        action_name = (
                            "billing_payment_recorded"
                            if locked.kind == BillingDocument.Kind.SALES_INVOICE
                            else "supplier_bill_payment_recorded"
                        )
                        log_billing_transaction(
                            request,
                            action_name,
                            payment,
                            locked,
                        )
                        messages.success(request, f"Payment recorded. Receipt {payment.receipt_number} is ready.")
                        return redirect("operations:billing_detail", pk=doc.pk)
        elif action == "record_refund":
            if doc.kind != BillingDocument.Kind.SALES_INVOICE:
                messages.error(request, "Supplier bill payments cannot be refunded from this workflow.")
            elif doc.status not in (
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ):
                messages.error(request, "Only issued invoices with a recorded payment can be refunded.")
            elif refund_form.is_valid():
                with transaction.atomic():
                    locked = BillingDocument.objects.select_for_update().get(pk=doc.pk)
                    payment = BillingPayment.objects.select_for_update().get(
                        pk=refund_form.cleaned_data["payment"].pk,
                        document=locked,
                    )
                    refunded = payment.refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0")
                    refund = refund_form.save(commit=False)
                    refundable_balance = payment.amount - refunded
                    if refund.amount > refundable_balance:
                        refund_form.add_error("amount", f"Refund cannot exceed {refundable_balance}.")
                    elif refund.amount > locked.amount_paid:
                        refund_form.add_error("amount", "Refund cannot exceed the invoice's net amount paid.")
                    else:
                        refund.recorded_by = request.user
                        refund.save()
                        locked.amount_paid -= refund.amount
                        locked.status = (
                            BillingDocument.Status.PART_PAID
                            if locked.amount_paid > 0
                            else BillingDocument.Status.ISSUED
                        )
                        locked.save(update_fields=["amount_paid", "status"])
                        log_billing_transaction(
                            request,
                            "billing_refund_recorded",
                            refund,
                            locked,
                            reason=refund.reason,
                        )
                        messages.success(request, "Refund recorded.")
                        return redirect("operations:billing_detail", pk=doc.pk)
        else:
            messages.error(request, "Choose a valid payment action.")
    return render_page(
        request,
        "operations/billing_detail.html",
        {
            "doc": doc,
            "payment_form": payment_form,
            "refund_form": refund_form,
            "refunds": BillingRefund.objects.filter(payment__document=doc).select_related(
                "payment", "recorded_by"
            ),
            "can_manage": billing_manager(request.user, profile),
            "has_approved_adjustments": doc.adjustments.filter(status="approved").exists(),
        },
    )


@login_required
def inventory_form(request, kind):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    definitions = {
        "item": (InventoryItemForm, "Add inventory item", "operations:inventory"),
        "warehouse": (WarehouseForm, "Add warehouse", "operations:inventory"),
        "supplier": (SupplierForm, "Add supplier", "operations:inventory"),
        "procurement": (ProcurementDocumentForm, "Create requisition or purchase order", "operations:inventory"),
        "movement": (StockMovementForm, "Record goods receipt or material consumption", "operations:inventory"),
    }
    if kind not in definitions: raise Http404
    form_class, title, return_url = definitions[kind]
    form = form_class(request.POST or None, factories=factories)
    if request.method == "POST" and form.is_valid():
        if kind == "movement":
            with transaction.atomic():
                movement = form.save(commit=False)
                item = InventoryItem.objects.select_for_update().get(pk=movement.item_id, factory__in=factories)
                warehouse_balance = inventory_balance(item, movement.warehouse)
                if movement.kind in (StockMovement.Kind.CONSUMPTION, StockMovement.Kind.ADJUST_OUT) and movement.quantity > warehouse_balance:
                    form.add_error("quantity", f"Only {warehouse_balance} {item.unit_of_measure} are available in this warehouse.")
                else:
                    movement.created_by = request.user
                    movement.save()
                    if movement.kind in (StockMovement.Kind.RECEIPT, StockMovement.Kind.ADJUST_IN) and movement.unit_cost:
                        item.standard_cost = movement.unit_cost
                        item.save(update_fields=["standard_cost"])
                    document = movement.procurement_document
                    if document and movement.kind == StockMovement.Kind.RECEIPT:
                        received = document.receipts.filter(kind=StockMovement.Kind.RECEIPT).aggregate(total=Sum("quantity"))["total"] or 0
                        document.status = ProcurementDocument.Status.RECEIVED if received >= document.quantity else ProcurementDocument.Status.PART_RECEIVED
                        document.save(update_fields=["status"])
                    log(request, "stock_movement_recorded", movement)
                    messages.success(request, "Stock movement recorded.")
                    return redirect(return_url)
        else:
            obj = form.save(commit=False)
            if kind == "procurement": obj.created_by = request.user
            obj.save()
            log(request, f"inventory_{kind}_created", obj)
            messages.success(request, "Inventory record saved.")
            return redirect(return_url)
    return render_page(request, "operations/form.html", {"form": form, "title": title})

@login_required
def home(request):
    profile, companies, factories, departments, units = scope(request.user)
    entries = scoped(ProductionEntry.objects.all(), profile, factories, departments, units)
    plans = scoped(ProductionPlan.objects.all(), profile, factories, departments, units)
    orders = scoped(WorkOrder.objects.all(), profile, factories, departments, units)
    today = timezone.localdate()
    daily = entries.filter(production_date=today).aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"))
    actual_today, rejected_today = daily["actual"] or 0, daily["rejected"] or 0
    planned_today = orders.filter(scheduled_start__date=today).aggregate(total=Sum("planned_quantity"))["total"] or 0
    trend = []
    for offset in reversed(range(7)):
        day = today - timedelta(days=offset)
        totals = entries.filter(production_date=day).aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"))
        trend.append({"day": day.strftime("%a"), "actual": totals["actual"] or 0, "rejected": totals["rejected"] or 0})
    factory_performance = list(entries.filter(production_date=today).values("factory__name").annotate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity")).order_by("factory__name"))
    for row in factory_performance:
        row["good"] = row["actual"] - row["rejected"]
        row["reject_rate"] = round(row["rejected"] * 100 / row["actual"], 1) if row["actual"] else 0
    return render_page(request, "operations/home.html", {
        "profile": profile, "today": today, "factory_count": factories.count(), "department_count": departments.count(),
        "unit_count": units.count(), "product_count": Product.objects.filter(is_active=True).count(),
        "active_plans": plans.exclude(status__in=[ProductionPlan.Status.COMPLETED, ProductionPlan.Status.CANCELLED]).count(),
        "open_orders": orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(),
        "actual_today": actual_today, "rejected_today": rejected_today, "planned_today": planned_today,
        "attainment_pct": round(actual_today * 100 / planned_today, 1) if planned_today else None,
        "yield_pct": round((actual_today - rejected_today) * 100 / actual_today, 1) if actual_today else None,
        "trend_json": json.dumps(trend), "factory_performance": factory_performance,
        "recent_entries": entries.select_related("factory", "production_unit", "product")[:10], "can_manage": manager(profile),
    })


@login_required
def digital_twin(request):
    """Factory layout and a lightweight, assumption-based capacity simulator."""
    profile, companies, factories, departments, units = scope(request.user)
    factory_id = request.GET.get("factory")
    factory = (factories.filter(pk=factory_id).first() if factory_id else None) or factories.first()
    if not factory:
        return render_page(request, "operations/digital_twin.html", {
            "factories": factories, "factory": None, "machine_rows": [],
            "scenario": {"demand": 1000, "hours": 8, "downtime": 5, "efficiency": 100},
            "title": "Industrial Digital Twin",
        })

    try:
        demand = max(1, min(10000000, int(request.GET.get("demand", 1000))))
    except (TypeError, ValueError):
        demand = 1000
    try:
        hours = max(1, min(24, int(request.GET.get("hours", 8))))
    except (TypeError, ValueError):
        hours = 8
    try:
        downtime = max(0, min(90, int(request.GET.get("downtime", 5))))
    except (TypeError, ValueError):
        downtime = 5
    try:
        efficiency = max(10, min(150, int(request.GET.get("efficiency", 100))))
    except (TypeError, ValueError):
        efficiency = 100

    factory_units = units.filter(department__factory=factory, is_active=True).select_related("department")
    from apps.intelligence.models import Machine
    assets = Machine.objects.filter(factory=factory)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE): assets = assets.filter(department__in=departments)
    available_assets = assets.exclude(status__in=[Machine.Status.INACTIVE, Machine.Status.BREAKDOWN, Machine.Status.MAINTENANCE])
    rated_hourly_capacity = available_assets.aggregate(total=Sum("capacity_per_hour"))["total"] or 0
    machine_rows = list(factory_units[:12])
    configured_capacity = float(factory.production_capacity or 0)
    if rated_hourly_capacity:
        baseline = float(rated_hourly_capacity * hours)
        capacity_basis = "sum of rated hourly capacity for available registered machines"
    elif configured_capacity:
        baseline = configured_capacity * hours / 8
        capacity_basis = "factory capacity setting scaled to operating hours"
    else:
        baseline = max(1, factory_units.count()) * 250 * hours / 8
        capacity_basis = "estimated from configured production units"
    baseline = round(baseline, 1)
    simulated = round(baseline * (1 - downtime / 100) * efficiency / 100, 1)
    utilization = round(demand * 100 / simulated, 1) if simulated else 0
    today = timezone.localdate()
    recent_output = scoped(ProductionEntry.objects.filter(factory=factory, production_date__gte=today - timedelta(days=6)), profile, factories, departments, units).aggregate(total=Sum("actual_quantity"))["total"] or 0
    return render_page(request, "operations/digital_twin.html", {
        "title": "Industrial Digital Twin", "factories": factories, "factory": factory,
        "machine_rows": machine_rows, "unit_count": factory_units.count(), "registered_machine_count": assets.count(), "unavailable_machine_count": assets.exclude(pk__in=available_assets.values("pk")).count(),
        "asset_rows": assets.select_related("department", "production_unit").order_by("asset_tag")[:100],
        "baseline_capacity": baseline, "simulated_capacity": simulated,
        "capacity_basis": capacity_basis, "utilization": utilization,
        "demand_met": simulated >= demand, "shortfall": max(0, round(demand - simulated)), "recent_output": recent_output,
        "scenario": {"demand": demand, "hours": hours, "downtime": downtime, "efficiency": efficiency},
    })

@login_required
def catalogue(request):
    profile, companies, factories, departments, units = scope(request.user); kind = request.GET.get("kind", "product")
    qs = ProductCategory.objects.all() if kind == "category" else Product.objects.select_related("category")
    query = request.GET.get("q", "").strip()
    if query: qs = qs.filter(Q(name__icontains=query) | Q(description__icontains=query) | (Q() if kind == "category" else Q(sku__icontains=query)))
    return render_page(request, "operations/catalogue.html", {"page_obj": Paginator(qs, 25).get_page(request.GET.get("page")), "kind": kind, "query": query, "can_manage": manager(profile)})

@login_required
def catalog_form(request, kind, pk=None):
    profile, *rest = scope(request.user)
    if not manager(profile): raise Http404
    category = kind == "category"; model = ProductCategory if category else Product; form_class = ProductCategoryForm if category else ProductForm
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save(); log(request, f"product_{kind}_{'updated' if instance else 'created'}", obj); messages.success(request, "Catalogue record saved."); return redirect("operations:catalogue")
    return render_page(request, "operations/form.html", {"form": form, "title": f"{'Edit' if instance else 'Add'} {'category' if category else 'product'}"})

@login_required
def plans(request):
    profile, companies, factories, departments, units = scope(request.user); qs = scoped(ProductionPlan.objects.select_related("factory", "department", "production_unit", "product"), profile, factories, departments, units); return render_page(request, "operations/plan_list.html", {"plans": Paginator(qs, 25).get_page(request.GET.get("page")), "can_manage": manager(profile)})

@login_required
def plan_form(request, pk=None):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    instance = get_object_or_404(scoped(ProductionPlan.objects.all(), profile, factories, departments, units), pk=pk) if pk else None
    form = ProductionPlanForm(request.POST or None, instance=instance, factories=factories, departments=departments, units=units)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.created_by = obj.created_by or request.user; obj.save(); log(request, "production_plan_saved", obj); messages.success(request, "Production plan saved."); return redirect("operations:plans")
    return render_page(request, "operations/form.html", {"form": form, "title": "Edit production plan" if instance else "Create production plan"})

@login_required
def orders(request):
    profile, companies, factories, departments, units = scope(request.user)
    qs = scoped(WorkOrder.objects.select_related("factory", "department", "production_unit", "product", "plan").annotate(actual_quantity=Sum("entries__actual_quantity"), rejected_quantity=Sum("entries__rejected_quantity")), profile, factories, departments, units).order_by("scheduled_start", "order_number")
    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))
    for order in page_obj:
        order.actual_quantity = order.actual_quantity or 0
        order.rejected_quantity = order.rejected_quantity or 0
        order.variance = order.actual_quantity - order.planned_quantity
        order.attainment_pct = round(order.actual_quantity * 100 / order.planned_quantity, 1) if order.planned_quantity else 0
    return render_page(request, "operations/order_list.html", {"orders": page_obj, "can_manage": manager(profile)})

@login_required
def order_form(request, pk=None):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    instance = get_object_or_404(scoped(WorkOrder.objects.all(), profile, factories, departments, units), pk=pk) if pk else None
    form = WorkOrderForm(request.POST or None, instance=instance, factories=factories, departments=departments, units=units)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.created_by = obj.created_by or request.user; obj.save(); log(request, "work_order_saved", obj); messages.success(request, "Work order saved."); return redirect("operations:orders")
    return render_page(request, "operations/form.html", {"form": form, "title": "Edit work order" if instance else "Create work order"})

@login_required
def entry_create(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    form = ProductionEntryForm(request.POST or None, factories=factories, departments=departments, units=units)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.entered_by = request.user; obj.save(); log(request, "production_entry_recorded", obj); messages.success(request, "Daily production recorded."); return redirect("operations:history")
    return render_page(request, "operations/form.html", {"form": form, "title": "Record daily production"})

@login_required
def history(request):
    profile, companies, factories, departments, units = scope(request.user); qs = scoped(ProductionEntry.objects.select_related("factory", "department", "production_unit", "product", "work_order"), profile, factories, departments, units)
    start, end = request.GET.get("start", ""), request.GET.get("end", "")
    try:
        if start: date.fromisoformat(start); qs = qs.filter(production_date__gte=start)
        else: start = ""
    except ValueError: start = ""
    try:
        if end: date.fromisoformat(end); qs = qs.filter(production_date__lte=end)
        else: end = ""
    except ValueError: end = ""
    selected_factory = request.GET.get("factory", "")
    if selected_factory.isdigit(): qs = qs.filter(factory_id=selected_factory)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8"); response["Content-Disposition"] = 'attachment; filename="production-history.csv"'; response.write("\ufeff"); writer = csv.writer(response); writer.writerow(["Date", "Factory", "Department", "Unit", "SKU", "Work order", "Actual", "Rejected", "Good", "Downtime minutes"])
        for row in qs: writer.writerow([row.production_date, row.factory.name, row.department.name, row.production_unit.name, row.product.sku, row.work_order.order_number if row.work_order else "", row.actual_quantity, row.rejected_quantity, row.good_quantity, row.downtime_minutes])
        return response
    return render_page(request, "operations/history.html", {"entries": Paginator(qs, 30).get_page(request.GET.get("page")), "start": start, "end": end, "can_manage": manager(profile)})

@login_required
def unit_performance(request):
    profile, companies, factories, departments, units = scope(request.user)
    entries = scoped(ProductionEntry.objects.all(), profile, factories, departments, units)
    raw = entries.values("production_unit_id", "production_unit__name", "production_unit__unit_code", "factory__name").annotate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"), entries=Count("id")).order_by("factory__name", "production_unit__name")
    summaries = []
    for row in raw:
        row["good"] = row["actual"] - row["rejected"]
        row["reject_rate"] = round(row["rejected"] * 100 / row["actual"], 1) if row["actual"] else 0
        summaries.append(row)
    return render_page(request, "operations/unit_performance.html", {"summaries": summaries})

@login_required
@require_POST
def order_status(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    if not manager(profile): raise Http404
    obj = get_object_or_404(scoped(WorkOrder.objects.all(), profile, factories, departments, units), pk=pk); status = request.POST.get("status")
    if status not in WorkOrder.Status.values: messages.error(request, "Invalid work order status.")
    else: obj.status = status; obj.save(update_fields=["status"]); log(request, "work_order_status_changed", obj); messages.success(request, "Work order status updated.")
    return redirect("operations:orders")
