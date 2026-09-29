from django import forms
from django.contrib.auth import authenticate, get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import AccountProfile
from .services import CLIENT_GROUP


User = get_user_model()


class RegistrationForm(forms.Form):
    role = forms.ChoiceField(label="Кем вы регистрируетесь?", choices=[
        (AccountProfile.Role.CLIENT, "Клиент"), (AccountProfile.Role.MASTER, "Мастер"),
    ], widget=forms.RadioSelect)
    first_name = forms.CharField(label="Имя", max_length=150)
    last_name = forms.CharField(label="Фамилия", max_length=150, required=False)
    phone = forms.CharField(label="Телефон", max_length=40)
    email = forms.EmailField(label="Email")
    password1 = forms.CharField(label="Пароль", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Повтор пароля", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.RadioSelect):
                field.widget.attrs["class"] = "account-input"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise ValidationError("Аккаунт с таким email уже существует.")
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password2") and cleaned["password1"] != cleaned["password2"]:
            self.add_error("password2", "Пароли не совпадают.")
        if cleaned.get("password1"):
            candidate = User(username=cleaned.get("email", ""), email=cleaned.get("email", ""), first_name=cleaned.get("first_name", ""), last_name=cleaned.get("last_name", ""))
            try:
                password_validation.validate_password(cleaned["password1"], candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            username=data["email"], email=data["email"], password=data["password1"],
            first_name=data["first_name"], last_name=data["last_name"], is_staff=False, is_superuser=False,
        )
        status = AccountProfile.Approval.PENDING if data["role"] == AccountProfile.Role.MASTER else AccountProfile.Approval.ACTIVE
        AccountProfile.objects.create(user=user, role=data["role"], approval_status=status, phone=data["phone"])
        if data["role"] == AccountProfile.Role.CLIENT:
            group, _ = Group.objects.get_or_create(name=CLIENT_GROUP)
            user.groups.add(group)
        return user


class AccountAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label="Email или логин", widget=forms.TextInput(attrs={"class": "account-input", "autocomplete": "username"}))
    password = forms.CharField(label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"class": "account-input", "autocomplete": "current-password"}))


class ClientProfileForm(forms.Form):
    first_name = forms.CharField(label="Имя", max_length=150)
    last_name = forms.CharField(label="Фамилия", max_length=150, required=False)
    email = forms.EmailField(label="Email")
    phone = forms.CharField(label="Телефон", max_length=40)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values(): field.widget.attrs["class"] = "account-input"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.exclude(pk=self.user.pk).filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise ValidationError("Аккаунт с таким email уже существует.")
        return email


class PhoneCodeForm(forms.Form):
    code = forms.RegexField(r"^\d{6}$", label="Код подтверждения", widget=forms.TextInput(attrs={"class": "account-input", "inputmode": "numeric", "autocomplete": "one-time-code"}))
