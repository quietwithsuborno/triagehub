from rest_framework.permissions import SAFE_METHODS, BasePermission
from accounts.models import User

class CanModifyTicket(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS or request.method == "POST":
            return True
        return request.user.role in (User.Role.AGENT, User.Role.ADMIN)