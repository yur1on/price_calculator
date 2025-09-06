"""Формы приложения repairs."""
from django import forms

from django import forms

class BookingForm(forms.Form):
    """Минимальная форма бронирования: имя, телефон, реф.код и согласие."""
    customer_name = forms.CharField(
        label="Ваше имя",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Иван Иванов"}),
    )
    customer_phone = forms.CharField(
        label="Номер телефона",
        max_length=20,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+375 XX XXX XX XX"}),
    )
    referral_code = forms.CharField(
        label="Код реферальный (необязательно)",
        max_length=16,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ABC123"}),
    )

    # ← НОВОЕ: обязательная галочка согласия
    consent = forms.BooleanField(
        required=True,
        label="",
        error_messages={"required": "Пожалуйста, подтвердите согласие с условиями."},
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_consent"})
    )
