from django.db import models
from apps.billing.validators import validate_gstin, validate_state_code


class Company(models.Model):
    name = models.CharField(max_length=200)
    registration_number = models.CharField(max_length=100, blank=True)
    industry_type = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Companies"


class Factory(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="factories"
    )
    name = models.CharField(max_length=200)
    factory_code = models.CharField(max_length=50)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    gstin = models.CharField(max_length=15, blank=True, validators=[validate_gstin])
    state_code = models.CharField(max_length=2, blank=True, validators=[validate_state_code])
    manager_name = models.CharField(max_length=150, blank=True)
    production_capacity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "factory_code"],
                name="unique_factory_code_per_company"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.factory_code})"


class Department(models.Model):
    factory = models.ForeignKey(
        Factory,
        on_delete=models.CASCADE,
        related_name="departments"
    )
    name = models.CharField(max_length=150)
    department_code = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    manager_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["factory", "department_code"],
                name="unique_department_code_per_factory"
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.factory.name}"


class ProductionUnit(models.Model):
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="production_units"
    )
    name = models.CharField(max_length=150)
    unit_code = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["department", "unit_code"],
                name="unique_unit_code_per_department"
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.department.name}"
