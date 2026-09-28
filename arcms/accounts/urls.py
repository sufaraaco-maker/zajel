from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify_view, name="verify"),
    path("2fa/", views.enroll_view, name="enroll"),
    path("password/", views.password_change_view, name="password_change"),
    path("logout/", views.logout_view, name="logout"),
]
