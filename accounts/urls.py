"""Auth URLs, mounted under /api/v1/auth/ in config/urls.py."""

from django.urls import path

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    # Username and password in, access token (short-lived) and refresh token out.
    path("login/", TokenObtainPairView.as_view(), name="login"),
    # Refresh token in, new access token out, without logging in again.
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
