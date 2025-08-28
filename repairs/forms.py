"""Формы приложения repairs."""
from django import forms


class BookingForm(forms.Form):
    """Минимальная форма бронирования: имя, телефон и необязательный реферальный код."""
    customer_name = forms.CharField(
        label="Ваше имя",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Иван Иванов"}),
    )
    customer_phone = forms.CharField(
        label="Номер телефона",
        max_length=20,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+421 9XX XXX XXX"}),
    )
    referral_code = forms.CharField(
        label="Код продавца (необязательно)",
        max_length=16,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ABC123"}),
    )
