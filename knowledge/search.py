"""Searching the documentation by meaning (Phase 5, Step 4).

The question gets an embedding too. The chunks whose embeddings point most
the same way as the question's are the ones most likely to answer it, in
any language. No answer is written here: that is Step 5, from these chunks.
"""

from dataclasses import dataclass

from django.conf import settings
from pgvector.django import CosineDistance

from .embeddings import embed_texts, get_embedding_client
from .models import DocChunk

MAX_QUESTION_CHARS = 1000  # a question, not a whole document


@dataclass
class SearchResult:
    """One chunk found for a question, and how close it is in meaning."""

    chunk: DocChunk
    score: float  # cosine similarity: 1 = same meaning, near 0 = unrelated


def search_chunks(question, limit=5):
    """Return the `limit` chunks closest in meaning to the question, best first.

    Only chunks embedded by the current model are searched: embeddings from
    two models cannot be compared.
    """
    question = question.strip()
    if not question:
        raise ValueError("the question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"the question is longer than {MAX_QUESTION_CHARS} characters")

    client = get_embedding_client()
    if client is None:
        raise RuntimeError(
            "AI is not set up: fill AI_BASE_URL, AI_API_KEY and EMBEDDING_MODEL "
            "in .env"
        )
    [question_vector], _ = embed_texts([question], client)

    chunks = (
        DocChunk.objects.filter(
            embedding__isnull=False, embedding_model=settings.EMBEDDING_MODEL
        )
        .select_related("page")
        .annotate(distance=CosineDistance("embedding", question_vector))
        .order_by("distance")[:limit]
    )
    # The database gives the distance (0 = same direction); 1 - distance is
    # the similarity, the same number as in the probe and the order check.
    return [SearchResult(chunk=chunk, score=1 - chunk.distance) for chunk in chunks]
