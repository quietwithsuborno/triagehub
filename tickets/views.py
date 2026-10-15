from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.models import User
from .models import Ticket
from .serializers import TicketSerializer, CommentSerializer
from .permissions import CanModifyTicket

class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    permission_classes = [CanModifyTicket]

    filterset_fields = ["status", "priority", "category"]
    search_fields = ["title", "description"]

    def get_queryset(self):
        user = self.request.user
        if user.role in (User.Role.AGENT, User.Role.ADMIN):
            return Ticket.objects.all()
        return Ticket.objects.filter(created_by = user)

    def perform_create(self, serializer):
        serializer.save(created_by = self.request.user)

    @action(
            detail = True, 
            methods = ["get","post"], 
            url_path = "comments",
            serializer_class = CommentSerializer,
    )
    def comments(self, request, pk = None):
        ticket = self.get_object()
        is_staff_role = request.user.role in (User.Role.AGENT, User.Role.ADMIN)

        if request.method == "POST":
            serializer = CommentSerializer(data = request.data)
            serializer.is_valid(raise_exception = True)

            if is_staff_role:
                serializer.save(ticket = ticket, author = request.user)
            else:
                serializer.save(ticket = ticket, author = request.user, is_internal = False)

            return Response(serializer.data, status = status.HTTP_201_CREATED)

        comments = ticket.comments.all()
        if not is_staff_role:
            comments = comments.filter(is_internal = False)

        serializer = CommentSerializer(comments, many = True)
        return Response(serializer.data)