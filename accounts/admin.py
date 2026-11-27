"""Django admin panel setup for users, including the role field."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from tickets.assignment import release_tickets
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Django's normal user admin, plus a "Role" section.

    This is the only place where an account's role can be changed.
    """

    # Add the role field to both the edit form and the "add user" form.
    fieldsets = UserAdmin.fieldsets + (("Role", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Role", {"fields": ("role",)}),)

    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = UserAdmin.list_filter + ("role",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # 6H-4: turning an account off frees its open tickets for the team.
        if change and "is_active" in form.changed_data and not obj.is_active:
            count = release_tickets(obj, changed_by=request.user)
            if count:
                self.message_user(
                    request,
                    f"{count} open ticket(s) of {obj.username} are now unassigned.",
                )
