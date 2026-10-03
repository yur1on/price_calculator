from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", views.account_logout, name="logout"),
    path("register/", views.register, name="register"),
    path("register/pending/", views.registration_pending, name="registration_pending"),
    path("password-reset/", auth_views.PasswordResetView.as_view(template_name="accounts/password_reset.html", email_template_name="accounts/password_reset_email.txt"), name="password_reset"),
    path("password-change/", auth_views.PasswordChangeView.as_view(template_name="accounts/password_change.html", success_url=reverse_lazy("accounts:client_profile")), name="password_change"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="accounts/password_reset_confirm.html"), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("workspace/", views.workspace, name="workspace"),
    path("client/", views.client_dashboard, name="client_dashboard"),
    path("client/referrals/", views.client_referrals, name="client_referrals"),
    path("client/repairs/", views.client_repairs, name="client_repairs"),
    path("client/appointments/", views.client_appointments, name="client_appointments"),
    path("client/appointments/<int:pk>/", views.client_appointment_detail, name="client_appointment_detail"),
    path("client/appointments/<int:pk>/cancel/", views.client_appointment_cancel, name="client_appointment_cancel"),
    path("client/repairs/<int:pk>/", views.client_repair_detail, name="client_repair_detail"),
    path("client/devices/", views.client_devices, name="client_devices"),
    path("client/warranties/", views.client_warranties, name="client_warranties"),
    path("client/profile/", views.client_profile, name="client_profile"),
    path("client/verify-phone/", views.phone_verify, name="phone_verify"),
    path("master/", views.master_dashboard, name="master_dashboard"),
    path("master/salary/", views.my_salary, name="my_salary"),
    path("master/salary/calculations/<int:pk>/", views.salary_calculation_detail, name="salary_calculation_detail"),
    path("master/stock/", views.master_stock, name="master_stock"),
    path("master/suppliers/", views.master_suppliers, name="master_suppliers"),
]
