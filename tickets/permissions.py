from rest_framework.permissions import SAFE_METHODS, BasePermission

class CanModifyTicket(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS or request.method == "POST":
            return True
        return request.user.is_support_staff