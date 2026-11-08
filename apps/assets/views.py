from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from .models import Asset, MaintenanceRecord


@login_required
def dashboard(request):
    assets = Asset.objects.all()

    stats = {
        "total_assets": assets.count(),
        "total_value": assets.aggregate(
            total=Sum("purchase_cost")
        )["total"] or 0,
        "available": assets.filter(status="AVAILABLE").count(),
        "assigned": assets.filter(status="ASSIGNED").count(),
        "maintenance": assets.filter(status="MAINTENANCE").count(),
        "disposed": assets.filter(status="DISPOSED").count(),
        "maintenance_records": MaintenanceRecord.objects.count(),
    }

    recent_assets = assets[:10]

    return render(request, "assets/dashboard.html", {
        "stats": stats,
        "recent_assets": recent_assets,
    })
