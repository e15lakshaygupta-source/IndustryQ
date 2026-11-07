from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

from apps.organization.models import Company, Department, Factory
from .models import UserProfile

User = get_user_model()


class PremiumAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}))


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save()
            UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.EMPLOYEE})
        return user


class ProfileForm(forms.ModelForm):
    email = forms.EmailField()
    company = forms.ModelChoiceField(queryset=Company.objects.all(), required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = UserProfile
        fields = (
            "company", "factory", "department", "job_title", "phone", "date_of_birth",
            "address", "bio", "emergency_contact_name", "emergency_contact_phone",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 3}),
            "bio": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["email"].initial = user.email
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        if self.instance.role == UserProfile.Role.SUPER_ADMIN:
            selected_company = self.data.get("company") or self.instance.company_id
            self.fields["factory"].queryset = Factory.objects.filter(company_id=selected_company) if selected_company else Factory.objects.all()
            selected_factory = self.data.get("factory") or self.instance.factory_id
            self.fields["department"].queryset = Department.objects.filter(factory_id=selected_factory) if selected_factory else Department.objects.all()
        else:
            self.fields["company"].queryset = Company.objects.filter(pk=self.instance.company_id)
            self.fields["company"].disabled = True
            self.fields["factory"].queryset = Factory.objects.filter(pk=self.instance.factory_id)
            self.fields["factory"].disabled = True
            self.fields["department"].queryset = Department.objects.filter(pk=self.instance.department_id)
            self.fields["department"].disabled = True

    def clean(self):
        cleaned = super().clean()
        company, factory, department = cleaned.get("company"), cleaned.get("factory"), cleaned.get("department")
        if factory and company and factory.company_id != company.pk:
            self.add_error("factory", "Choose a factory within the selected company.")
        if department and factory and department.factory_id != factory.pk:
            self.add_error("department", "Choose a department within the selected factory.")
        if self.instance.role != UserProfile.Role.SUPER_ADMIN:
            cleaned["company"], cleaned["factory"], cleaned["department"] = self.instance.company, self.instance.factory, self.instance.department
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=False)
        self.user.email = self.cleaned_data["email"]
        self.user.first_name = self.cleaned_data.get("first_name", self.user.first_name)
        self.user.last_name = self.cleaned_data.get("last_name", self.user.last_name)
        if commit:
            self.user.save()
            profile.company = self.cleaned_data.get("company")
            profile.factory = self.cleaned_data.get("factory")
            profile.department = self.cleaned_data.get("department")
            profile.factory_name = profile.factory.name if profile.factory else ""
            profile.department_name = profile.department.name if profile.department else ""
            profile.save()
        return profile


class ManagedUserForm(RegistrationForm):
    role = forms.ChoiceField(choices=UserProfile.Role.choices)
    factory = forms.ModelChoiceField(queryset=Factory.objects.none(), required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False)

    def __init__(self, *args, actor_profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor_profile = actor_profile
        allowed_roles = {
            UserProfile.Role.SUPER_ADMIN: list(UserProfile.Role.choices),
            UserProfile.Role.COMPANY_ADMIN: [c for c in UserProfile.Role.choices if c[0] not in (UserProfile.Role.SUPER_ADMIN, UserProfile.Role.COMPANY_ADMIN)],
            UserProfile.Role.FACTORY_MANAGER: [(UserProfile.Role.DEPARTMENT_MANAGER, "Department Manager"), (UserProfile.Role.EMPLOYEE, "Employee")],
            UserProfile.Role.DEPARTMENT_MANAGER: [(UserProfile.Role.EMPLOYEE, "Employee")],
        }
        self.fields["role"].choices = allowed_roles.get(actor_profile.role, [])
        factories = Factory.objects.all()
        companies = Company.objects.all()
        if actor_profile.role == UserProfile.Role.COMPANY_ADMIN:
            companies = companies.filter(pk=actor_profile.company_id)
            factories = factories.filter(company__in=companies)
            self.fields["company"].initial = actor_profile.company
            self.fields["company"].disabled = True
        self.fields["company"].queryset = companies
        if actor_profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER):
            factories = factories.filter(pk=actor_profile.factory_id)
            self.fields["factory"].initial = actor_profile.factory
        self.fields["factory"].queryset = factories
        departments = Department.objects.filter(factory__in=factories)
        if actor_profile.role == UserProfile.Role.DEPARTMENT_MANAGER:
            departments = departments.filter(pk=actor_profile.department_id)
            self.fields["department"].initial = actor_profile.department
        self.fields["department"].queryset = departments

    def clean(self):
        cleaned = super().clean()
        factory, department = cleaned.get("factory"), cleaned.get("department")
        if department and factory and department.factory_id != factory.pk:
            self.add_error("department", "Choose a department within the selected factory.")
        if self.actor_profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER):
            if factory != self.actor_profile.factory:
                self.add_error("factory", "You can only assign users to your own factory.")
        if self.actor_profile.role == UserProfile.Role.COMPANY_ADMIN:
            if factory and factory.company_id != self.actor_profile.company_id:
                self.add_error("factory", "You can only assign users within your company.")
            cleaned["company"] = self.actor_profile.company
        if self.actor_profile.role == UserProfile.Role.DEPARTMENT_MANAGER and department != self.actor_profile.department:
            self.add_error("department", "You can only assign users to your own department.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            profile = user.profile
            profile.role = self.cleaned_data["role"]
            profile.factory = self.cleaned_data["factory"]
            profile.department = self.cleaned_data["department"]
            profile.company = self.cleaned_data.get("company") or (profile.factory.company if profile.factory else None)
            profile.factory_name = profile.factory.name if profile.factory else ""
            profile.department_name = profile.department.name if profile.department else ""
            profile.save()
        return user


class UserAssignmentForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("role", "company", "factory", "department")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.order_by("name")
        self.fields["factory"].queryset = Factory.objects.select_related("company").order_by("company__name", "name")
        self.fields["department"].queryset = Department.objects.select_related("factory").order_by("factory__name", "name")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        company = cleaned.get("company")
        factory = cleaned.get("factory")
        department = cleaned.get("department")

        if factory and company and factory.company_id != company.pk:
            self.add_error("factory", "Choose a factory within the selected company.")
        if department and factory and department.factory_id != factory.pk:
            self.add_error("department", "Choose a department within the selected factory.")
        if department and not factory:
            self.add_error("factory", "Select the department's factory.")
        if factory and not company:
            cleaned["company"] = factory.company

        if role == UserProfile.Role.COMPANY_ADMIN and not cleaned.get("company"):
            self.add_error("company", "A company is required for a company admin.")
        elif role == UserProfile.Role.FACTORY_MANAGER and not factory:
            self.add_error("factory", "A factory is required for a factory manager.")
        elif role == UserProfile.Role.DEPARTMENT_MANAGER and not department:
            self.add_error("department", "A department is required for a department manager.")

        if role == UserProfile.Role.SUPER_ADMIN:
            cleaned["company"] = None
            cleaned["factory"] = None
            cleaned["department"] = None
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.company = self.cleaned_data.get("company")
        profile.factory = self.cleaned_data.get("factory")
        profile.department = self.cleaned_data.get("department")
        profile.factory_name = profile.factory.name if profile.factory else ""
        profile.department_name = profile.department.name if profile.department else ""
        if commit:
            profile.save()
        return profile


class SecurePasswordChangeForm(PasswordChangeForm):
    pass
