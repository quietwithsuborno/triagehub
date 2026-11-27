"""What happens to an agent's tickets when the account is turned off (6H)."""

from django.db import transaction

from .history import record_changes, snapshot
from .models import Ticket

WORKING = [Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS]


def release_tickets(agent, changed_by):
    """Unassign the agent's open and in-progress tickets, and write each to history.

    Resolved and closed tickets keep the agent, because that is who solved them.
    Returns how many tickets were released.
    """
    released = 0
    with transaction.atomic():
        tickets = Ticket.all_objects.filter(assigned_to=agent, status__in=WORKING)
        for ticket in tickets:
            before = snapshot(ticket)
            ticket.assigned_to = None
            ticket.save(update_fields=["assigned_to", "updated_at"])
            record_changes(ticket, before, changed_by)
            released += 1
    return released
