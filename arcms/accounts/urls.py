from django.urls import path

from . import views, views_keys

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify_view, name="verify"),
    path("2fa/", views.enroll_view, name="enroll"),
    path("keys/", views_keys.keys_page, name="keys"),
    path("keys/register/begin", views_keys.register_begin, name="key_register_begin"),
    path("keys/register/finish", views_keys.register_finish, name="key_register_finish"),
    path("verify/key/begin", views_keys.login_begin, name="key_login_begin"),
    path("verify/key/finish", views_keys.login_finish, name="key_login_finish"),
    path("password/", views.password_change_view, name="password_change"),
    path("sessions/end/", views.sessions_end_view, name="sessions_end"),
    path("logout/", views.logout_view, name="logout"),
]
