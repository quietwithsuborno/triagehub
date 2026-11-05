"""Serializer for creating new accounts."""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from .models import User


class RegisterSerializer(serializers.ModelSerializer):
    """Sign-up data: username, email and password.

    "role" is not in fields, so a client cannot register as an agent or admin.
    Every new account gets the default role (user).
    """

    # write_only: the password is accepted but never sent back in a response.
    # validate_password runs the AUTH_PASSWORD_VALIDATORS from settings
    # (minimum length, too common, all numbers). The "too close to the
    # username" check needs the user object, so it does not run here yet.
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def create(self, validated_data):
        # create_user hashes the password. A plain create() would store it
        # as readable text and login would never work.
        return User.objects.create_user(**validated_data)
