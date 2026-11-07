from django.db import models

# Create your models here.
from django.conf import settings
from django.db import models


class SavedKPIDashboard(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_kpi_dashboards")
    name = models.CharField(max_length=100)
    metric_keys = models.JSONField(default=list)
    factory = models.ForeignKey("organization.Factory", null=True, blank=True, on_delete=models.SET_NULL, related_name="saved_kpi_dashboards")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="unique_kpi_dashboard_name_per_user")]

    def __str__(self):
        return self.name
