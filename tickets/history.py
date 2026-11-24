"""Writing a ticket's history: what changed, from what, to what, and by whom."""

from .models import TicketChange


def snapshot(ticket):
    """The values we keep history of, as plain text, right now.

    Taken before and after an update; the difference is the history. Names are
    saved, not ids, so the history still reads correctly if a category is
    renamed or a user is deleted later.
    """
    return {
        TicketChange.Field.STATUS: ticket.status,
        TicketChange.Field.PRIORITY: ticket.priority,
        TicketChange.Field.CATEGORY: ticket.category.name if ticket.category else "",
        TicketChange.Field.ASSIGNED_TO: (
            ticket.assigned_to.username if ticket.assigned_to else ""
        ),
    }


def record_changes(ticket, before, changed_by):
    """Save one TicketChange for every value that differs from `before`.

    `before` is a snapshot taken before the change. `changed_by` is the user,
    or None when the AI or the system made the change. Returns the saved rows.
    """
    after = snapshot(ticket)
    rows = [
        TicketChange(
            ticket=ticket,
            field_name=field_name,
            old_value=before[field_name],
            new_value=new_value,
            changed_by=changed_by,
        )
        for field_name, new_value in after.items()
        if before[field_name] != new_value
    ]
    # One INSERT for all rows, not one per row.
    return TicketChange.objects.bulk_create(rows)
