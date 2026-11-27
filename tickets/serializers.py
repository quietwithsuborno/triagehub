"""Serializers: convert tickets, comments and categories to and from JSON."""

from rest_framework import serializers
from .models import Ticket, Comment, Category, TicketChange


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
        # Every description is sent to the AI, so a limit keeps the cost of one
        # ticket small and the text within what the AI can read. 5000
        # characters is about two pages, plenty for a support request.
        extra_kwargs = {"description": {"max_length": 5000}}

    def validate_assigned_to(self, value):
        """Only agents and admins can be assigned a ticket.

        DRF calls validate_<field> automatically. A 400 is returned before
        anything is saved. None is allowed, it means "unassigned".
        """
        if value is not None and not value.is_support_staff:
            raise serializers.ValidationError(
                "Tickets can only be assigned to an agent or admin."
            )

        if value is not None and not value.is_active:
            raise serializers.ValidationError(
                "This account is inactive. Assign the ticket to someone else."
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


class TicketChangeSerializer(serializers.ModelSerializer):
    """One line of a ticket's history. All fields are read-only: the history
    is written by the code, never by a request.

    changed_by_username is null when the AI or the system made the change.
    """

    changed_by_username = serializers.CharField(
        source="changed_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = TicketChange
        fields = [
            "id",
            "field_name",
            "old_value",
            "new_value",
            "changed_by_username",
            "created_at",
        ]
        read_only_fields = fields


class ReopenSerializer(serializers.Serializer):
    """What POST /tickets/{id}/reopen/ takes: why the ticket is opened again.

    The reason becomes a public comment, so the agent sees what went wrong.
    Spaces at both ends are trimmed first, so "   " counts as empty (400).
    """

    reason = serializers.CharField(max_length=2000)


class TriageValuesSerializer(serializers.Serializer):
    """Category and priority: what the ticket has, or what the AI suggests."""

    category = serializers.IntegerField(allow_null=True)
    category_name = serializers.CharField(allow_null=True)
    priority = serializers.CharField()


class RetriageSerializer(serializers.Serializer):
    """The answer of POST /tickets/{id}/retriage/.

    Nothing in it is saved on the ticket except ai_summary. To use the
    suggestion, the agent PATCHes the ticket, which also writes the history.
    """

    suggestion = TriageValuesSerializer()
    current = TriageValuesSerializer()
    ai_summary = serializers.CharField()


class StatusCountsSerializer(serializers.Serializer):
    open = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    resolved = serializers.IntegerField()
    closed = serializers.IntegerField()


class PriorityCountsSerializer(serializers.Serializer):
    low = serializers.IntegerField()
    medium = serializers.IntegerField()
    high = serializers.IntegerField()
    urgent = serializers.IntegerField()


class CategoryCountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    count = serializers.IntegerField()


class TicketNumbersSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    uncategorized = serializers.IntegerField()
    unassigned = serializers.IntegerField()
    new_last_7_days = serializers.IntegerField()
    by_status = StatusCountsSerializer()
    by_priority = PriorityCountsSerializer()
    by_category = CategoryCountSerializer(many=True)


class ResolutionSerializer(serializers.Serializer):
    resolved = serializers.IntegerField()
    without_history = serializers.IntegerField()
    avg_hours = serializers.FloatField(allow_null=True)
    reopened = serializers.IntegerField()


class AITriageCountsSerializer(serializers.Serializer):
    success = serializers.IntegerField()
    error = serializers.IntegerField()
    invalid = serializers.IntegerField()


class StatsSerializer(serializers.Serializer):
    tickets = TicketNumbersSerializer()
    resolution = ResolutionSerializer()
    ai_triage = AITriageCountsSerializer()
