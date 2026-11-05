from rest_framework import serializers
from .models import Ticket, Comment, Category
from accounts.models import User

class TicketSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    created_by_role = serializers.CharField(source="created_by.role",read_only=True )
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)

    class Meta:
        model = Ticket
        fields = [
            'id',
            'title',
            'description',
            'status',
            'priority',
            'category',
            'category_name',
            'created_by',
            'created_by_username',
            "created_by_role",
            'assigned_to',
            'ai_summary',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            "id",
            "created_by",
            "ai_summary",
            "created_at",
            "updated_at",
        ]

    def validate_assigned_to(self, value):
        if value is not None and value.role not in (User.Role.AGENT, User.Role.ADMIN):
            raise serializers.ValidationError(
                "Tickets can only be assigned to an agent or admin."
            )
        return value

class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source = "author.username", read_only = True)
    author_role = serializers.CharField(source = "author.role", read_only = True)

    class Meta:
        model = Comment
        fields = [
            'id',
            'ticket',
            'author',
            'author_username',
            'author_role',
            'body',
            'is_internal',
            'created_at',
        ]
        read_only_fields = [
            "id",
            "ticket",
            "author",
            "created_at",
        ]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]