from django.contrib import admin
from .models import Category, Ticket, Comment

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

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

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "is_internal", "created_at")
    list_filter = ("is_internal",)
    search_fields = ("body",)