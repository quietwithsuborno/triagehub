"""Database models for the helpdesk: categories, tickets and comments."""

from django.conf import settings
from django.db import models


class Category(models.Model):
    """A topic a ticket can belong to, such as Billing or Bug.

    Categories are managed from the Django admin panel. The API only lists them.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Categories"  # admin panel shows "Categorys" otherwise
        ordering = ["name"]

    def __str__(self):
        return self.name


class TicketManager(models.Manager):
    """Ticket.objects: only tickets that are not deleted (6G-2)."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Ticket(models.Model):
    """A support request opened by a customer and handled by the support team."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    # SET_NULL: deleting a category keeps its tickets, they just lose the label.
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    # CASCADE: if a user account is deleted, their tickets go with it.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_tickets",
    )
    # SET_NULL: if an agent leaves, the ticket stays and becomes unassigned.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )

    # Short summary to be written by the AI triage in Phase 4. Users cannot set
    # it through the API (read-only in the serializer).

    ai_summary = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_tickets",
    )

    objects = TicketManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]  # newest tickets first

    def __str__(self):
        return f"#{self.pk} {self.title}"


class Comment(models.Model):
    """A message on a ticket.

    Internal comments (is_internal=True) are private notes for agents and
    admins. Customers never see them, and cannot create them.
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Oldest first, like a chat. "id" breaks ties when two comments share
        # the same timestamp, so the order is always the same.
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Comment by {self.author} on ticket #{self.ticket_id}"


class TicketChange(models.Model):
    """One change to one field of a ticket. All rows together are its history.

    Values are saved as text (a category's name, not its id), so the history
    still shows what it was even after a category or user is renamed or
    deleted. changed_by is empty when the AI or the system made the change.
    """

    class Field(models.TextChoices):
        STATUS = "status", "Status"
        PRIORITY = "priority", "Priority"
        CATEGORY = "category", "Category"
        ASSIGNED_TO = "assigned_to", "Assigned to"
        TITLE = "title", "Title"
        DESCRIPTION = "description", "Description"

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="changes",
    )
    field_name = models.CharField(max_length=20, choices=Field.choices)
    # TextField, not CharField: same speed in PostgreSQL, and a description's
    # history (S6) can be long.
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    # SET_NULL: the history stays even if the user is deleted.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_changes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Oldest first, like a timeline. "id" keeps the order fixed when one
        # update changes two fields at the same moment.
        ordering = ["created_at", "id"]

    def __str__(self):
        return (
            f"#{self.ticket_id} {self.field_name}: {self.old_value} -> {self.new_value}"
        )


class AILog(models.Model):
    """One record for every call to the AI, successful or not.

    Used to debug bad answers, to see why a fallback happened, and to measure
    the real token usage (and so the real cost) of the AI features.
    """

    class Purpose(models.TextChoices):
        TRIAGE = "triage", "Triage"  # Phase 5 adds more purposes here
        IMAGE_DESCRIPTION = "image_description", "Image description"
        EMBEDDING = "embedding", "Embedding"
        ANSWER = "answer", "Answer"
        OVERVIEW = "overview", "Module overview"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"  # valid answer, used on the ticket
        ERROR = "error", "Error"  # no answer: timeout, network, bad key
        INVALID = "invalid", "Invalid"  # answer came back but failed validation

    # SET_NULL: deleting a ticket keeps its logs, so cost history stays complete.
    # null=True also allows logs that belong to no ticket (Phase 5 questions).
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_logs",
    )

    purpose = models.CharField(max_length=20, choices=Purpose.choices)

    status = models.CharField(max_length=20, choices=Status.choices)

    provider = models.CharField(max_length=50, blank=True)

    model_name = models.CharField(max_length=100, blank=True)

    # Token counts and timing are unknown when the call fails, hence null.
    input_tokens = models.PositiveIntegerField(null=True, blank=True)

    output_tokens = models.PositiveIntegerField(null=True, blank=True)

    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    # The AI's raw text, kept so an INVALID answer can be inspected later.
    raw_response = models.TextField(blank=True)

    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]  # newest first
        verbose_name = "AI log"

    def __str__(self):
        return f"{self.purpose} {self.status} (ticket #{self.ticket_id})"
