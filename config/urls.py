from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("accounts/", include("arcms.accounts.urls")),
    path("studio/", include("arcms.studio.urls")),
    path("", include("arcms.public.urls")),
]

if settings.DEBUG or settings.TESTING or getattr(settings, "ARCMS_SERVE_MEDIA", False):
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]

handler404 = "arcms.public.views.not_found"
handler500 = "arcms.public.views.server_error"
handler403 = "arcms.studio.views_admin.forbidden"
