from rest_framework import serializers
from .models import Ticket, Comment

class TicketSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    created_by_role = serializers.CharField(source="created_by.role",read_only=True )
    class Meta:
        model = Ticket
        fields = [
            'id',
            'title',
            'description',
            'status',
            'priority',
            'category',
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

class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Comment
        fields = [
            'id',
            'ticket',
            'author',
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