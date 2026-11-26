"""Permission rules for the ticket API."""

from rest_framework.permissions import SAFE_METHODS, BasePermission


class CanModifyTicket(BasePermission):
    """Everyone logged in can read and create; only support staff can change.

    - GET (read) and POST (create a ticket or comment): any logged-in user.
    - PUT, PATCH, DELETE: agents and admins only. Customers get 403.

    Which tickets a person can see at all is decided in
    TicketViewSet.get_queryset, not here.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # SAFE_METHODS are the read-only ones: GET, HEAD and OPTIONS.
        if request.method in SAFE_METHODS or request.method == "POST":
            return True
        return request.user.is_support_staff


class IsSupportStaff(BasePermission):
    """Agents and admins only; customers get 403.

    The same rule exists in knowledge/views.py. It is written again here, not
    imported, because knowledge already imports from tickets, and importing
    both ways can break (circular import). See journal 6D-6 and W7.
    """

    message = "Only support staff can do this."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_support_staff
