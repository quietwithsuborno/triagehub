"""URLs of the knowledge assistant, under /api/v1/ (see config/urls.py)."""

from django.urls import path

from .views import AskView, ModuleOverviewDetail, ModuleOverviewList, SuggestedReplyView

urlpatterns = [
    path("knowledge/ask/", AskView.as_view(), name="knowledge-ask"),
    path(
        "knowledge/modules/", ModuleOverviewList.as_view(), name="module-overview-list"
    ),
    path(
        "knowledge/modules/<slug:module>/",
        ModuleOverviewDetail.as_view(),
        name="module-overview-detail",
    ),
    path(
        "tickets/<int:pk>/suggested-reply/",
        SuggestedReplyView.as_view(),
        name="suggested-reply",
    ),
]
