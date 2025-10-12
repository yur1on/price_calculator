# repairs/middleware.py
from .models import PageView

class AnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.method == "GET" and not request.path.startswith('/admin'):
            PageView.objects.create(
                path=request.path,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                ip_address=self._get_ip(request),
                referer=request.META.get("HTTP_REFERER", "")[:500],
            )
        return response

    def _get_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")
