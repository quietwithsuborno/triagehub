"""User model for TriageHub.

Every account has one role (user, agent or admin). The role decides what the
account can see and do in the API.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A Django user with an extra role field."""

    class Role(models.TextChoices):
        USER = "user", "User"  # a customer who opens tickets
        AGENT = "agent", "Agent"  # support team member who handles tickets
        ADMIN = "admin", "Admin"  # support team lead with full API access

    # New accounts always start as a customer. The register API ignores any
    # "role" it receives; roles are changed only from the Django admin panel.
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.USER,
    )

    @property
    def is_support_staff(self):
        """True for agents and admins, the people who work on tickets.

        This is the single place that decides who counts as support staff.
        Views, permissions and serializers all ask this property, so adding
        a new staff role later means changing only this line.

        Not to be confused with Django's built-in is_staff, which only means
        "can log in to the admin panel".
        """
        return self.role in {self.Role.AGENT, self.Role.ADMIN}

    def __str__(self):
        return f"{self.username} ({self.role})"