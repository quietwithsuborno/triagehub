from rest_framework import viewsets
from accounts.models import User
from .models import Ticket
from .serializers import TicketSerializer
from .permissions import CanModifyTicket

class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    permission_classes = [CanModifyTicket]

    def get_queryset(self):
        user = self.request.user
        if user.role in (User.Role.AGENT, User.Role.ADMIN):
            return Ticket.objects.all()
        return Ticket.objects.filter(created_by = user)

    def perform_create(self, serializer):
        serializer.save(created_by = self.request.user)