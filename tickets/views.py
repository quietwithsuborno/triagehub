"""API views for tickets, their comments, and categories."""

import logging
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from rest_framework.throttling import UserRateThrottle
from .models import Ticket, Category, TicketChange, Comment
from .serializers import (
    TicketSerializer,
    CommentSerializer,
    CategorySerializer,
    TicketChangeSerializer,
    ReopenSerializer,
    RetriageSerializer,
    StatsSerializer,
)
from .permissions import CanModifyTicket, IsSupportStaff
from .triage import triage_ticket, run_triage, get_ai_client
from .history import snapshot, record_changes
from .stats import build_stats

logger = logging.getLogger(__name__)


class TicketCreateThrottle(UserRateThrottle):
    """Limits how many tickets one user can create ("ticket_create" rate)."""

    scope = "ticket_create"


class RetriageThrottle(UserRateThrottle):
    """Limits how many re-triages one person asks for: each one uses the AI."""

    scope = "retriage"


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
        throttles = super().get_throttles()
        if self.action == "create":
            throttles.append(TicketCreateThrottle())
        elif self.action == "retriage":
            throttles.append(RetriageThrottle())
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

    def perform_update(self, serializer):
        """Save the change and write its history: both or neither.

        The old values are read before saving, because save() overwrites
        them on the same object. Inside one transaction, so a failure while
        writing the history also undoes the change itself.
        """
        with transaction.atomic():
            before = snapshot(serializer.instance)
            ticket = serializer.save()
            record_changes(ticket, before, self.request.user)

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

    @action(
        detail=True,
        methods=["get"],
        serializer_class=TicketChangeSerializer,
    )
    def history(self, request, pk=None):
        """List what changed on a ticket, oldest first.

        Customers see only status changes: priority and assignee are internal
        matters, like internal notes. Filtered on the server, because hiding
        them in the UI would not be enough.
        """
        # get_object uses get_queryset, so a customer gets 404 for a ticket
        # that is not theirs, before anything else happens.
        ticket = self.get_object()

        changes = ticket.changes.select_related("changed_by")
        if not request.user.is_support_staff:
            changes = changes.filter(field_name=TicketChange.Field.STATUS)

        serializer = TicketChangeSerializer(changes, many=True)
        return Response(serializer.data)

    @extend_schema(request=ReopenSerializer, responses={200: TicketSerializer})
    @action(
        detail=True,
        methods=["post"],
        serializer_class=ReopenSerializer,
    )
    def reopen(self, request, pk=None):
        """Open a resolved ticket again, with a reason: POST {"reason": "..."}.

        The ticket's owner or support staff can do it, and only on a resolved
        ticket. In one transaction: the status goes back to open (the assignee
        stays), the reason becomes a public comment, and the history records it.
        """
        # A customer gets 404 here for a ticket that is not theirs.
        ticket = self.get_object()
        serializer = ReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            # Read the ticket again with a lock on its row. A second request
            # at the same moment (a double click) waits here, then sees "open".
            # No select_related: PostgreSQL does not lock across a join with
            # a foreign key that can be empty.
            ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if ticket.status != Ticket.Status.RESOLVED:
                return Response(
                    {
                        "detail": "Only a resolved ticket can be reopened. "
                        f"This ticket is {ticket.status}."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            before = snapshot(ticket)
            ticket.status = Ticket.Status.OPEN
            ticket.save(update_fields=["status", "updated_at"])
            Comment.objects.create(
                ticket=ticket,
                author=request.user,
                body=serializer.validated_data["reason"],
            )
            record_changes(ticket, before, request.user)

        data = TicketSerializer(ticket, context=self.get_serializer_context()).data
        return Response(data)

    @extend_schema(request=None, responses={200: RetriageSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsSupportStaff])
    def retriage(self, request, pk=None):
        """Ask the AI again for a category and priority. Support staff only.

        Only a suggestion: category and priority are not changed. The agent
        PATCHes the ticket to use it, and that PATCH writes the history. The
        summary belongs to the AI alone, so it is updated here directly.
        Every AI call is written to AILog; if the AI fails, 503.
        """
        ticket = self.get_object()
        result = run_triage(ticket, get_ai_client())
        if result is None:  # AI off, too slow, or an unusable answer
            return Response(
                {"detail": "The AI could not triage this ticket now. Try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        ticket.ai_summary = result.summary
        ticket.save(update_fields=["ai_summary", "updated_at"])

        # filter(name=None) matches nothing, so "no category fits" gives None.
        suggested = Category.objects.filter(name=result.category_name).first()
        data = {
            "suggestion": {
                "category": suggested.id if suggested else None,
                "category_name": suggested.name if suggested else None,
                "priority": result.priority,
            },
            "current": {
                "category": ticket.category_id,
                "category_name": ticket.category.name if ticket.category else None,
                "priority": ticket.priority,
            },
            "ai_summary": ticket.ai_summary,
        }
        return Response(RetriageSerializer(data).data)


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


class StatsView(APIView):
    """Numbers for the staff dashboard (step 6E)."""

    permission_classes = [IsSupportStaff]

    @extend_schema(responses={200: StatsSerializer})
    def get(self, request):
        return Response(StatsSerializer(build_stats()).data)
