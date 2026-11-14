"""API views for tickets, their comments, and categories."""

import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from .models import Ticket, Category
from .serializers import TicketSerializer, CommentSerializer, CategorySerializer
from .permissions import CanModifyTicket
from .triage import triage_ticket

logger = logging.getLogger(__name__)


class TicketCreateThrottle(UserRateThrottle):
    """Limits how many tickets one user can create ("ticket_create" rate)."""

    scope = "ticket_create"


class TicketViewSet(viewsets.ModelViewSet):
    """List, create, view, update and delete tickets.

    /api/v1/tickets/ and /api/v1/tickets/{id}/, plus
    /api/v1/tickets/{id}/comments/ for the comments of one ticket.
    """

    serializer_class = TicketSerializer
    permission_classes = [CanModifyTicket]

    # ?status=open&priority=high&category=2 and ?search=word
    filterset_fields = ["status", "priority", "category"]
    search_fields = ["title", "description"]

    def get_throttles(self):
        """The general limits for every request, plus a stricter one on create.

        Each new ticket may call the paid AI, so creating has its own limit.
        """
        throttles = super().get_throttles()
        if self.action == "create":
            throttles.append(TicketCreateThrottle())
        return throttles

    def get_queryset(self):
        """Support staff see every ticket; customers see only their own.

        A customer asking for someone else's ticket gets 404, not 403, so
        they cannot even find out that the ticket exists.
        """
        # drf-spectacular calls this without a real logged-in user while it
        # builds the Swagger docs. An anonymous user has no role, so return
        # an empty queryset instead of crashing.
        if getattr(self, "swagger_fake_view", False):
            return Ticket.objects.none()
        user = self.request.user
        # select_related loads the creator and category in the same query,
        # so a list of 10 tickets is 1 query instead of 1 + 2 per ticket (N+1).
        queryset = Ticket.objects.select_related("created_by", "category")

        if user.is_support_staff:
            return queryset
        return queryset.filter(created_by=user)

    def perform_create(self, serializer):
        """Save a new ticket with the logged-in user as its creator.

        Customers cannot triage their own ticket: status, priority, category
        and assignee are reset to safe defaults whatever they sent. Agents and
        admins keep the values they chose (for example a ticket taken by phone).

        Then the AI triage fills in what it may (see triage_ticket). It runs
        after saving because the AI log needs the ticket's id, and it never
        stops the ticket from being created.
        """
        user = self.request.user
        if user.is_support_staff:
            ticket = serializer.save(created_by=user)
        else:
            ticket = serializer.save(
                created_by=user,
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.MEDIUM,
                category=None,
                assigned_to=None,
            )

        # triage_ticket already survives any AI failure. This also covers a bug
        # in our own triage code or a database error: the ticket is already
        # saved, so the customer must get 201, not 500 (after a 500 they would
        # send the same ticket again).
        try:
            triage_ticket(ticket)
        except Exception:
            logger.exception("AI triage failed for ticket %s", ticket.pk)
            # Show what is really in the database, not half-changed values.
            ticket.refresh_from_db()

    @action(
        detail=True,
        methods=["get", "post"],
        serializer_class=CommentSerializer,
    )
    def comments(self, request, pk=None):
        """GET lists a ticket's comments, POST adds one.

        Internal notes are for support staff only: customers never see them,
        and a customer's comment is always saved as public.
        """
        # get_object uses get_queryset, so a customer gets 404 for a ticket
        # that is not theirs, before anything else happens.
        ticket = self.get_object()

        if request.method == "POST":
            serializer = CommentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Ticket comes from the URL and author from the login, never from
            # the request body.
            if request.user.is_support_staff:
                serializer.save(ticket=ticket, author=request.user)
            else:
                serializer.save(ticket=ticket, author=request.user, is_internal=False)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        comments = ticket.comments.select_related("author")

        # Filtered on the server: hiding internal notes in the UI would not be
        # enough, because anyone can call the API directly.
        if not request.user.is_support_staff:
            comments = comments.filter(is_internal=False)

        serializer = CommentSerializer(comments, many=True)
        return Response(serializer.data)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only list of categories for any logged-in user.

    ReadOnlyModelViewSet gives only list and retrieve, so POST, PUT and DELETE
    return 405. Categories are managed from the Django admin panel.
    """

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    # There are only a few categories and a dropdown needs all of them,
    # so return a plain list instead of pages.
    pagination_class = None
