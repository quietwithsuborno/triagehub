"""The knowledge assistant's API: ask a question, get an answer from the docs."""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .answer import AnswerValidationError, answer_question
from .models import ModuleOverview
from .serializers import AnswerSerializer, AskSerializer, ModuleOverviewSerializer

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
