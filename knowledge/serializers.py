"""The shapes of the knowledge assistant's API: the question and the answer."""

from rest_framework import serializers

from .models import ModuleOverview
from .search import MAX_QUESTION_CHARS


class AskSerializer(serializers.Serializer):
    """What the client sends. Spaces are trimmed; an empty question is refused."""

    question = serializers.CharField(max_length=MAX_QUESTION_CHARS)


class SourceSerializer(serializers.Serializer):
    title = serializers.CharField()
    url = serializers.URLField()


class AnswerSerializer(serializers.Serializer):
    """What the client gets back."""

    found = serializers.BooleanField()
    answer = serializers.CharField()
    sources = SourceSerializer(many=True)


class ModuleOverviewSerializer(serializers.ModelSerializer):
    """A module's summary (both languages) and its Mermaid flowchart."""

    class Meta:
        model = ModuleOverview
        # source_hash and model_name stay inside: they are for us, not readers.
        fields = ["module", "summary_en", "summary_bn", "flowchart", "updated_at"]


class SuggestedReplySerializer(serializers.Serializer):
    """A reply draft for a ticket: for the agent to check, change and send."""

    found = serializers.BooleanField()
    reply = serializers.CharField(allow_blank=True)
    sources = SourceSerializer(many=True)
