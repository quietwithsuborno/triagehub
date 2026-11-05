from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Ticket, Category
from .serializers import TicketSerializer, CommentSerializer, CategorySerializer
from .permissions import CanModifyTicket

class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    permission_classes = [CanModifyTicket]

    filterset_fields = ["status", "priority", "category"]
    search_fields = ["title", "description"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ticket.objects.none()
        user = self.request.user
        queryset = Ticket.objects.select_related("created_by", "category")

        if user.is_support_staff:
            return queryset
        return queryset.filter(created_by = user)

        if user.is_support_staff:
            return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_support_staff:
            serializer.save(created_by = user)
        else:
            serializer.save(
                created_by = user,
                status = Ticket.Status.OPEN,
                priority = Ticket.Priority.MEDIUM,
                category = None,
                assigned_to = None,
            )
        if user.is_support_staff:
            serializer.save(created_by = user)

    @action(
            detail = True, 
            methods = ["get","post"], 
            url_path = "comments",
            serializer_class = CommentSerializer,
    )
    
    def comments(self, request, pk = None):
        ticket = self.get_object()

        if request.method == "POST":
            serializer = CommentSerializer(data = request.data)
            serializer.is_valid(raise_exception = True)

            if request.user.is_support_staff:
                serializer.save(ticket = ticket, author = request.user)
            else:
                serializer.save(ticket = ticket, author = request.user, is_internal = False)

            return Response(serializer.data, status = status.HTTP_201_CREATED)

        comments = ticket.comments.select_related("author")

        if not request.user.is_support_staff:
            comments = comments.filter(is_internal = False)

        serializer = CommentSerializer(comments, many = True)
        return Response(serializer.data)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = None