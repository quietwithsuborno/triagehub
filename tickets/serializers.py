from rest_framework import serializers
from .models import Ticket, Comment

class TicketSerializer(serializers.ModelSerializer):
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