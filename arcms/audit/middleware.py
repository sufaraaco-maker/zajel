from arcms.core.utils import client_ip

from .services import Actor, reset_actor, set_actor


class AuditContextMiddleware:
    """يربط كل تغيير يحدث أثناء الطلب بالمستخدم وعنوانه."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = set_actor(
            Actor(
                user=user if user is not None and user.is_authenticated else None,
                ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                label=str(user) if user is not None and user.is_authenticated else "زائر",
            )
        )
        try:
            return self.get_response(request)
        finally:
            reset_actor(token)
