"""Django admin panel setup for categories, tickets and comments."""

from django.contrib import admin
from .models import Category, Ticket, Comment


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


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
    )
    list_filter = ("status", "priority", "category")
    search_fields = ("title", "description")
    # The list shows these related objects on every row; load them in one
    # query instead of one extra query per row (N+1).
    list_select_related = ("category", "created_by", "assigned_to")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "is_internal", "created_at")
    list_filter = ("is_internal",)
    search_fields = ("body",)
    list_select_related = ("ticket", "author")  # same N+1 fix as TicketAdmin
