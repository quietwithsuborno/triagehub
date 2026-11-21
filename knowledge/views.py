"""The knowledge assistant's API: ask a question, get an answer from the docs."""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from rest_framework.permissions import BasePermission

from django.shortcuts import get_object_or_404
from .answer import AnswerValidationError, answer_question
from .models import ModuleOverview
from .serializers import (
    AnswerSerializer,
    AskSerializer,
    ModuleOverviewSerializer,
    SuggestedReplySerializer,
)

from tickets.models import Ticket
from .search import MAX_QUESTION_CHARS

logger = logging.getLogger(__name__)

UNAVAILABLE = "The assistant cannot answer right now. Please try again later."


def unavailable():
    """503: the assistant cannot answer now; the client may try again later."""
    return Response({"detail": UNAVAILABLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class AskThrottle(UserRateThrottle):
    """Limits how many questions one user can ask ("knowledge_ask" rate)."""

    scope = "knowledge_ask"


class AskView(APIView):
    """Answer a question from the PrismERP documentation, with source links.

    Only logged-in users (the default permission). The answer comes only
    from the documentation; when it has no answer, found is false.
    """

    def get_throttles(self):
        """The general limits, plus a stricter one: each question uses the AI."""
        return super().get_throttles() + [AskThrottle()]

    @extend_schema(
        request=AskSerializer,
        responses={200: AnswerSerializer},
        summary="Ask the documentation a question",
        tags=["Knowledge"],
    )
    def post(self, request):
        ask = AskSerializer(data=request.data)
        ask.is_valid(raise_exception=True)  # a bad question gets 400
        try:
            answer = answer_question(ask.validated_data["question"])
        except AnswerValidationError:
            # The AI answered, but not in a way we can trust (already in AILog).
            return unavailable()
        except Exception:
            # The AI is down, slow, out of free quota, or not set up. A clear
            # "try later" is better than a guessed answer.
            logger.exception("The knowledge assistant could not answer.")
            return unavailable()
        data = {"found": answer.found, "answer": answer.text, "sources": answer.sources}
        return Response(AnswerSerializer(data).data)


@extend_schema(tags=["Knowledge"], summary="List the module overviews")
class ModuleOverviewList(generics.ListAPIView):
    """Every module's overview (summary and flowchart). Made in advance by
    build_overviews, so reading it costs no AI call."""

    queryset = ModuleOverview.objects.all()
    serializer_class = ModuleOverviewSerializer


@extend_schema(tags=["Knowledge"], summary="One module's overview")
class ModuleOverviewDetail(generics.RetrieveAPIView):
    """One module's overview, found by the module's name, e.g. supply-chain."""

    queryset = ModuleOverview.objects.all()
    serializer_class = ModuleOverviewSerializer
    lookup_field = "module"


class IsSupportStaff(BasePermission):
    """Agents and admins only (customers get 403)."""

    message = "Only support staff can ask for a reply draft."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_support_staff


class SuggestReplyThrottle(UserRateThrottle):
    """Limits how many reply drafts one person asks for: each uses the AI."""

    scope = "suggest_reply"


def ticket_question(ticket):
    """The ticket as a question for the documentation: its title, then its
    description, cut to the length a question may have."""
    return f"{ticket.title}\n{ticket.description}"[:MAX_QUESTION_CHARS]


@extend_schema(
    request=None,
    responses={200: SuggestedReplySerializer},
    summary="Draft a reply to a ticket from the documentation",
    tags=["Knowledge"],
)
class SuggestedReplyView(APIView):
    """A reply draft for one ticket, written from the documentation.

    For support staff only. Nothing is sent to the customer: the agent reads
    the draft, changes it if needed, and posts it as a comment. When the
    documentation has no answer, found is false and there is no draft.
    """

    permission_classes = [IsSupportStaff]

    def get_throttles(self):
        return super().get_throttles() + [SuggestReplyThrottle()]

    def post(self, request, pk):
        ticket = get_object_or_404(Ticket, pk=pk)
        try:
            answer = answer_question(ticket_question(ticket))
        except Exception:
            logger.exception("Reply draft failed for ticket %s", ticket.pk)
            return unavailable()
        data = {
            "found": answer.found,
            # Our "not found" message is written for customers; the agent
            # just needs to know there is no draft.
            "reply": answer.text if answer.found else "",
            "sources": answer.sources,
        }
        return Response(SuggestedReplySerializer(data).data)
