from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import UserProfile
from apps.operations.models import Customer
from apps.organization.models import Factory
from apps.operations.views import scope as operations_scope
from apps.accounts.models import UserActivity
from .forms import (
    EnquiryForm,
    FollowUpForm,
    LeadForm,
    QuotationForm,
    QuotationLineFormSet,
)
from .models import Enquiry, FollowUp, Lead, Quotation


def crm_data(user):
    profile, companies, factories, departments, units = operations_scope(user)
    if profile.role in (
        UserProfile.Role.FACTORY_MANAGER,
        UserProfile.Role.DEPARTMENT_MANAGER,
        UserProfile.Role.EMPLOYEE,
    ) and not profile.factory_id:
        factories = factories.none()
    leads = Lead.objects.filter(factory__in=factories).select_related(
        "factory", "customer", "owner"
    )
    if profile.role == UserProfile.Role.EMPLOYEE:
        leads = leads.filter(Q(owner=user) | Q(created_by=user))
    customers = Customer.objects.filter(
        company__factories__in=factories, is_active=True
    ).distinct()
    enquiries = Enquiry.objects.filter(lead__in=leads).select_related(
        "lead", "factory"
    )
    quotations = Quotation.objects.filter(lead__in=leads).select_related(
        "lead", "factory"
    ).prefetch_related("lines")
    follow_ups = FollowUp.objects.filter(factory__in=factories).filter(
        Q(lead__in=leads) | Q(customer__in=customers)
    ).select_related("lead", "customer", "assigned_to", "factory")
    if profile.role == UserProfile.Role.EMPLOYEE:
        follow_ups = follow_ups.filter(Q(assigned_to=user) | Q(created_by=user))
    return profile, factories, leads, customers, enquiries, quotations, follow_ups


@login_required
def dashboard(request):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    stage_rows = [
        {
            "stage": stage,
            "label": label,
            "count": leads.filter(stage=stage).count(),
            "value": leads.filter(stage=stage).aggregate(total=Sum("estimated_value"))["total"]
            or Decimal("0"),
        }
        for stage, label in Lead.Stage.choices
    ]
    open_leads = leads.exclude(stage__in=[Lead.Stage.WON, Lead.Stage.LOST])
    overdue_follow_ups = follow_ups.filter(
        status=FollowUp.Status.OPEN, due_at__lt=timezone.now()
    ).count()
    customer_count = customers.count()
    return render(request, "crm/dashboard.html", {
        "title": "Customer relationship management",
        "profile": profile,
        "leads": leads[:12],
        "customers": customers.order_by("name")[:12],
        "customer_count": customer_count,
        "enquiries": enquiries[:8],
        "quotations": quotations[:8],
        "follow_ups": follow_ups[:10],
        "stage_rows": stage_rows,
        "lead_stages": Lead.Stage.choices,
        "enquiry_statuses": Enquiry.Status.choices,
        "quotation_statuses": Quotation.Status.choices,
        "pipeline_value": open_leads.aggregate(total=Sum("estimated_value"))["total"] or Decimal("0"),
        "open_lead_count": open_leads.count(),
        "open_follow_up_count": follow_ups.filter(status=FollowUp.Status.OPEN).count(),
        "overdue_follow_up_count": overdue_follow_ups,
        "can_manage": profile.role != UserProfile.Role.EMPLOYEE,
    })


@login_required
def lead_create(request):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    form = LeadForm(request.POST or None, factories=factories)
    if request.method == "POST" and form.is_valid():
        lead = form.save(commit=False)
        lead.created_by = request.user
        lead.owner = request.user
        lead.save()
        UserActivity.objects.create(
            user=request.user,
            action="crm_lead_created",
            details={"record_id": lead.pk, "company_name": lead.company_name},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"Lead for {lead.company_name} added to the sales pipeline.")
        return redirect("crm:dashboard")
    return render(request, "crm/form.html", {
        "title": "Add sales lead",
        "form": form,
        "submit_label": "Add lead",
        "cancel_url": reverse("crm:dashboard"),
    })


@login_required
def enquiry_create(request):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    form = EnquiryForm(request.POST or None, leads=leads)
    if request.method == "POST" and form.is_valid():
        enquiry = form.save(commit=False)
        enquiry.factory = enquiry.lead.factory
        enquiry.created_by = request.user
        enquiry.save()
        UserActivity.objects.create(
            user=request.user,
            action="crm_enquiry_created",
            details={"record_id": enquiry.pk, "subject": enquiry.subject},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "Customer enquiry recorded.")
        return redirect("crm:dashboard")
    return render(request, "crm/form.html", {
        "title": "Record customer enquiry",
        "form": form,
        "submit_label": "Record enquiry",
        "cancel_url": reverse("crm:dashboard"),
    })


@login_required
def quotation_create(request):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    form = QuotationForm(request.POST or None, leads=leads)
    formset = QuotationLineFormSet(
        request.POST or None,
        instance=Quotation(),
        prefix="lines",
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            lead = form.cleaned_data["lead"]
            factory = Factory.objects.select_for_update().get(pk=lead.factory_id)
            today = timezone.localdate()
            prefix = f"QT-{factory.factory_code}-{today:%Y%m%d}-"
            last_number = Quotation.objects.filter(
                factory=factory, number__startswith=prefix
            ).order_by("-number").values_list("number", flat=True).first()
            sequence = int(last_number.rsplit("-", 1)[-1]) + 1 if last_number else 1
            quotation = form.save(commit=False)
            quotation.factory = factory
            quotation.number = f"{prefix}{sequence:04d}"
            quotation.created_by = request.user
            quotation.save()
            formset.instance = quotation
            formset.save()
            UserActivity.objects.create(
                user=request.user,
                action="crm_quotation_created",
                details={"record_id": quotation.pk, "number": quotation.number},
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        messages.success(request, f"Quotation {quotation.number} created.")
        return redirect("crm:dashboard")
    return render(request, "crm/quotation_form.html", {
        "title": "Create sales quotation",
        "form": form,
        "formset": formset,
        "cancel_url": reverse("crm:dashboard"),
    })


@login_required
def follow_up_create(request):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    users = get_user_model().objects.filter(is_active=True).filter(
        Q(profile__factory__in=factories)
        | Q(profile__company_id__in=factories.values_list("company_id", flat=True))
        | Q(profile__role=UserProfile.Role.SUPER_ADMIN)
    ).distinct()
    form = FollowUpForm(
        request.POST or None,
        leads=leads,
        customers=customers,
        users=users,
    )
    if request.method == "POST" and form.is_valid():
        follow_up = form.save(commit=False)
        lead = form.cleaned_data.get("lead")
        customer = form.cleaned_data.get("customer")
        if lead:
            follow_up.factory = lead.factory
        else:
            follow_up.factory = factories.filter(company_id=customer.company_id).first()
            if follow_up.factory is None:
                form.add_error("customer", "No accessible factory is associated with this customer.")
        if not form.errors:
            follow_up.created_by = request.user
            if not follow_up.assigned_to_id:
                follow_up.assigned_to = request.user
            follow_up.save()
            UserActivity.objects.create(
                user=request.user,
                action="crm_follow_up_created",
                details={"record_id": follow_up.pk, "subject": follow_up.subject},
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, "Follow-up added to your task list.")
            return redirect("crm:dashboard")
    return render(request, "crm/form.html", {
        "title": "Schedule follow-up",
        "form": form,
        "submit_label": "Schedule follow-up",
        "cancel_url": reverse("crm:dashboard"),
    })


@login_required
@require_POST
def follow_up_complete(request, pk):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    follow_up = get_object_or_404(follow_ups, pk=pk)
    follow_up.status = FollowUp.Status.COMPLETED
    follow_up.save(update_fields=["status"])
    UserActivity.objects.create(
        user=request.user,
        action="crm_follow_up_completed",
        details={"record_id": follow_up.pk, "subject": follow_up.subject},
        ip_address=request.META.get("REMOTE_ADDR"),
    )
    messages.success(request, "Follow-up marked complete.")
    return redirect("crm:dashboard")


@login_required
@require_POST
def lead_stage_update(request, pk):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    lead = get_object_or_404(leads, pk=pk)
    stage = request.POST.get("stage")
    if stage not in Lead.Stage.values:
        messages.error(request, "Choose a valid sales stage.")
    else:
        lead.stage = stage
        lead.save(update_fields=["stage", "updated_at"])
        UserActivity.objects.create(
            user=request.user,
            action="crm_lead_stage_updated",
            details={"record_id": lead.pk, "stage": stage},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"{lead.company_name} moved to {lead.get_stage_display()}.")
    return redirect("crm:dashboard")


@login_required
@require_POST
def enquiry_status_update(request, pk):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    enquiry = get_object_or_404(enquiries, pk=pk)
    status = request.POST.get("status")
    if status not in Enquiry.Status.values:
        messages.error(request, "Choose a valid enquiry status.")
    else:
        enquiry.status = status
        enquiry.save(update_fields=["status"])
        UserActivity.objects.create(
            user=request.user,
            action="crm_enquiry_status_updated",
            details={"record_id": enquiry.pk, "status": status},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "Enquiry status updated.")
    return redirect("crm:dashboard")


@login_required
@require_POST
def quotation_status_update(request, pk):
    profile, factories, leads, customers, enquiries, quotations, follow_ups = crm_data(
        request.user
    )
    quotation = get_object_or_404(quotations, pk=pk)
    status = request.POST.get("status")
    if status not in Quotation.Status.values:
        messages.error(request, "Choose a valid quotation status.")
    else:
        quotation.status = status
        quotation.save(update_fields=["status"])
        UserActivity.objects.create(
            user=request.user,
            action="crm_quotation_status_updated",
            details={"record_id": quotation.pk, "number": quotation.number, "status": status},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "Quotation status updated.")
    return redirect("crm:dashboard")
