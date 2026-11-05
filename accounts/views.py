"""API view for creating an account. Login and refresh come from SimpleJWT."""

from rest_framework import generics
from rest_framework.permissions import AllowAny

from .serializers import RegisterSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/v1/auth/register/ creates a new customer account."""

    serializer_class = RegisterSerializer
    # Open to everyone: you cannot be logged in before you have an account.
    permission_classes = [AllowAny]
    # No authentication at all on this endpoint. Otherwise an expired or
    # broken token in the request would cause a 401 even though signing up
    # needs no token.
    authentication_classes = []
