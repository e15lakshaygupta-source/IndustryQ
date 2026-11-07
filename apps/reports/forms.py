from django import forms
from django.utils import timezone
from .models import SavedKPIDashboard


class ReportFilterForm(forms.Form):
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    factory = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start") and cleaned.get("end") and cleaned["end"] < cleaned["start"]:
            self.add_error("end", "End date must be on or after start date.")
        return cleaned


class SavedKPIDashboardForm(forms.ModelForm):
    class Meta:
        model = SavedKPIDashboard
        fields = ("name",)
