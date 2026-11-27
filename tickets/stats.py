"""Numbers for the staff dashboard, GET /api/v1/stats/ (step 6E)."""

from datetime import timedelta

from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
)
from django.utils import timezone

from .models import AILog, Category, Ticket, TicketChange

DONE = [Ticket.Status.RESOLVED, Ticket.Status.CLOSED]
WORKING = [Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS]


def build_stats(now=None):
    now = now or timezone.now()
    return {
        "tickets": ticket_numbers(now),
        "resolution": resolution_numbers(),
        "ai_triage": count_by(
            AILog.objects.filter(purpose=AILog.Purpose.TRIAGE),
            "status",
            AILog.Status.values,
        ),
    }


def count_by(queryset, field, values):
    rows = queryset.order_by().values(field).annotate(n=Count("id"))
    found = {row[field]: row["n"] for row in rows}
    return {value: found.get(value, 0) for value in values}


def ticket_numbers(now):
    numbers = Ticket.objects.aggregate(
        total=Count("id"),
        uncategorized=Count("id", filter=Q(category__isnull=True)),
        unassigned=Count("id", filter=Q(status__in=WORKING, assigned_to__isnull=True)),
        new_last_7_days=Count("id", filter=Q(created_at__gte=now - timedelta(days=7))),
    )
    numbers["by_status"] = count_by(
        Ticket.objects.all(), "status", Ticket.Status.values
    )
    numbers["by_priority"] = count_by(
        Ticket.objects.all(), "priority", Ticket.Priority.values
    )
    numbers["by_category"] = [
        {"id": category.id, "name": category.name, "count": category.n}
        for category in Category.objects.annotate(n=Count("tickets")).order_by("name")
    ]
    return numbers


def resolution_numbers():
    last_done_at = (
        TicketChange.objects.filter(
            ticket=OuterRef("pk"),
            field_name=TicketChange.Field.STATUS,
            new_value__in=DONE,
        )
        .order_by("-created_at", "-id")
        .values("created_at")[:1]
    )
    done = Ticket.objects.filter(status__in=DONE).annotate(
        done_at=Subquery(last_done_at)
    )
    numbers = done.aggregate(
        resolved=Count("id"),
        without_history=Count("id", filter=Q(done_at__isnull=True)),
        avg_time=Avg(
            ExpressionWrapper(
                F("done_at") - F("created_at"), output_field=DurationField()
            )
        ),
    )
    avg_time = numbers.pop("avg_time")
    numbers["avg_hours"] = (
        round(avg_time.total_seconds() / 3600, 1) if avg_time is not None else None
    )
    numbers["reopened"] = TicketChange.objects.filter(
        field_name=TicketChange.Field.STATUS,
        old_value__in=DONE,
        new_value__in=WORKING,
    ).aggregate(n=Count("ticket", distinct=True))["n"]
    return numbers
