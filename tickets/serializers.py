from rest_framework import serializers
from .models import Ticket

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