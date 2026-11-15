"""Django admin panel setup for the documentation knowledge base."""

from django.contrib import admin
from .models import DocChunk, DocPage


class ReadOnlyAdmin(admin.ModelAdmin):
    """Shows records without letting anyone add or edit them by hand.

    Pages and chunks are written only by the fetch command. A hand edit would
    no longer match the page's fingerprint or the chunk's embedding. Deleting
    is allowed: the next fetch builds them again.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DocPage)
class DocPageAdmin(ReadOnlyAdmin):
    list_display = ("title", "module", "url", "fetched_at")
    list_filter = ("module",)
    search_fields = ("title", "url")


@admin.register(DocChunk)
class DocChunkAdmin(ReadOnlyAdmin):
    list_display = ("page", "position", "heading", "has_embedding")
    list_filter = ("page__module",)
    search_fields = ("heading", "text")
    list_select_related = ("page",)  # the page title on every row, in one query
    exclude = ("embedding",)  # hundreds of numbers, unreadable on screen

    @admin.display(boolean=True)
    def has_embedding(self, obj):
        """A tick in the list when the chunk already has its embedding."""
        return obj.embedding is not None
