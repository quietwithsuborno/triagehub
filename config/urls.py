"""Top-level URLs: admin panel, the versioned API, and the API docs."""

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    # "v1" in the path lets a future v2 change the API without breaking
    # clients that still use v1.
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/", include("tickets.urls")),
    # OpenAPI schema (machine-readable) and the Swagger page built from it.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
