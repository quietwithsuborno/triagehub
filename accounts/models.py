from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        USER = "user", "User"
        AGENT = "agent", "Agent"
        ADMIN = "admin", "Admin"
    role = models.CharField(
        max_length = 10,
        choices = Role.choices,
        default = Role.USER,
    )
    @property
    def is_support_staff(self):
        return self.role in {self.Role.AGENT, self.Role.ADMIN}
    
    def __str__(self):
        return f"{self.username} ({self.role})"