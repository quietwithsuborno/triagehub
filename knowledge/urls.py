"""URLs of the knowledge assistant, under /api/v1/ (see config/urls.py)."""

from django.urls import path

from .views import AskView

urlpatterns = [
    path("knowledge/ask/", AskView.as_view(), name="knowledge-ask"),
]
