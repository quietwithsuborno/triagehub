"""Serializers: convert tickets, comments and categories to and from JSON."""

from rest_framework import serializers
from .models import Ticket, Comment, Category


class TicketSerializer(serializers.ModelSerializer):
    """A ticket as the API sends and receives it.

    Fields the server decides (id, created_by, ai_summary, timestamps) are
    read-only, so whatever a client sends for them is ignored. For customers,
    the view also overrides status, priority, category and assigned_to on
    create (see TicketViewSet.perform_create).
    """

    # Extra read-only fields so a client gets names, not only ids, and does
    # not need a second request to show who opened the ticket.
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    created_by_role = serializers.CharField(source="created_by.role", read_only=True)
    # allow_null: a ticket without a category returns null instead of failing.
    category_name = serializers.CharField(
        source="category.name", read_only=True, allow_null=True
    )

    class Meta:
        model = Ticket
        fields = [
            "id",
            "title",
            "description",
            "status",
            "priority",
            "category",
            "category_name",
            "created_by",
            "created_by_username",
            "created_by_role",
            "assigned_to",
            "ai_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "ai_summary",
            "created_at",
            "updated_at",
        ]

    def validate_assigned_to(self, value):
        """Only agents and admins can be assigned a ticket.

        DRF calls validate_<field> automatically. A 400 is returned before
        anything is saved. None is allowed, it means "unassigned".
        """
        if value is not None and not value.is_support_staff:
            raise serializers.ValidationError(
                "Tickets can only be assigned to an agent or admin."
            )
        return value


class CommentSerializer(serializers.ModelSerializer):
    """A comment on a ticket.

    ticket and author are read-only: the view takes the ticket from the URL
    and the author from the logged-in user, so nobody can comment as someone
    else or on a ticket they cannot see.
    """

    author_username = serializers.CharField(source="author.username", read_only=True)
    author_role = serializers.CharField(source="author.role", read_only=True)

    class Meta:
        model = Comment
        fields = [
            "id",
            "ticket",
            "author",
            "author_username",
            "author_role",
            "body",
            "is_internal",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "ticket",
            "author",
            "created_at",
        ]


class CategorySerializer(serializers.ModelSerializer):
    """Just id and name, enough for a filter or dropdown in the UI."""

    class Meta:
        model = Category
        fields = ["id", "name"]
