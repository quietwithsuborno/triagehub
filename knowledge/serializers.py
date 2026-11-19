"""The shapes of the knowledge assistant's API: the question and the answer."""

from rest_framework import serializers

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
