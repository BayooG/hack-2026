from django import forms
from django.contrib.auth.forms import BaseUserCreationForm

from .models import User


class SignupForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class SettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("agent_enabled", "work_start", "work_end")
        widgets = {
            "agent_enabled": forms.CheckboxInput(attrs={"role": "switch"}),
            "work_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "work_end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("work_start"), cleaned.get("work_end")
        if start and end and start >= end:
            raise forms.ValidationError("Working hours must end after they start.")
        return cleaned
