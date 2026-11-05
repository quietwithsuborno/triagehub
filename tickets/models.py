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

    created_at = models.DateTimeField(auto_now_add=True)  # set once, on create
    updated_at = models.DateTimeField(auto_now=True)  # refreshed on every save

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