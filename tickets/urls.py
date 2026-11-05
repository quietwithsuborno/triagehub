"""URLs for tickets and categories, mounted under /api/v1/ in config/urls.py."""

from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, CategoryViewSet

# The router builds all the list and detail URLs from each viewset, including
# the extra tickets/{id}/comments/ URL from the @action.
router = DefaultRouter()
router.register("tickets", TicketViewSet, basename="ticket")
router.register("categories", CategoryViewSet, basename="category")
urlpatterns = router.urls
