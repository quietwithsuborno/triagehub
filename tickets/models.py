from django.conf import settings
from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']

    def __str__(self):
        return self.name

class Ticket(models.Model):
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
         max_length = 20,
         choices = Status.choices,
         default = Status.OPEN,
    )
    priority = models.CharField(
         max_length = 20,
         choices = Priority.choices,
         default = Priority.MEDIUM,
    )
    category = models.ForeignKey(
         Category,
         on_delete = models.SET_NULL,
         null = True,
         blank = True,
         related_name = "tickets",             
    )
    created_by = models.ForeignKey(
         settings.AUTH_USER_MODEL,
         on_delete = models.CASCADE,
         related_name = "created_tickets",
    )
    assigned_to = models.ForeignKey(
         settings.AUTH_USER_MODEL,
         on_delete = models.SET_NULL,
         null = True,
         blank = True,
         related_name = "assigned_tickets",
    )
    ai_summary = models.CharField(max_length=500, blank=True, null=True)
    ai_summary = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
         return f"#{self.pk} {self.title}"