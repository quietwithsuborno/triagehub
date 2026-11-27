"""Django admin panel setup for categories, tickets and comments."""

from django.contrib import admin
from .models import Category, Ticket, Comment, AILog, TicketChange


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class DeletedFilter(admin.SimpleListFilter):
    """Right-side filter: deleted or not (6G-5)."""

    title = "deleted"
    parameter_name = "deleted"

    def lookups(self, request, model_admin):
        return [("no", "Not deleted"), ("yes", "Deleted")]

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(deleted_at__isnull=True)
        if self.value() == "yes":
            return queryset.filter(deleted_at__isnull=False)
        return queryset


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "status",
        "priority",
        "category",
        "created_by",
        "assigned_to",
        "created_at",
        "deleted_at",
    )
    list_filter = ("status", "priority", "category", DeletedFilter)
    search_fields = ("title", "description")
    # The list shows these related objects on every row; load them in one
    # query instead of one extra query per row (N+1).
    list_select_related = ("category", "created_by", "assigned_to")
    # Deleting happens only through the API, restoring only through the
    # action below, so the "who" is never lost (6G-8).
    readonly_fields = ("deleted_at", "deleted_by")
    actions = ["restore"]

    def get_queryset(self, request):
        # Ticket.objects hides deleted tickets; the admin must see them to
        # restore them (6G-5).
        queryset = Ticket.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset

    @admin.action(description="Restore selected tickets")
    def restore(self, request, queryset):
        count = queryset.filter(deleted_at__isnull=False).update(
            deleted_at=None, deleted_by=None
        )
        self.message_user(request, f"Restored {count} ticket(s).")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "is_internal", "created_at")
    list_filter = ("is_internal",)
    search_fields = ("body",)
    list_select_related = ("ticket", "author")  # same N+1 fix as TicketAdmin


@admin.register(AILog)
class AILogAdmin(admin.ModelAdmin):
    """Read-only view of AI calls: a log is a record of what happened."""

    list_display = (
        "id",
        "purpose",
        "status",
        "ticket",
        "model_name",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "created_at",
    )

    list_filter = ("purpose", "status")
    list_select_related = ("ticket",)

    # Logs are written by the code only. Nobody adds or edits them by hand,
    # otherwise they could no longer be trusted. Deleting old logs is allowed.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TicketChange)
class TicketChangeAdmin(admin.ModelAdmin):
    """Read-only view of ticket history: it is a record of what happened."""

    list_display = (
        "id",
        "ticket",
        "field_name",
        "old_value",
        "new_value",
        "changed_by",
        "created_at",
    )
    list_filter = ("field_name",)
    list_select_related = ("ticket", "changed_by")

    # Written by the code only, like AILog. Delete stays allowed on purpose:
    # without it, Django admin would refuse to delete any ticket that has
    # history, even for a superuser (see journal 6B, step 1b).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
