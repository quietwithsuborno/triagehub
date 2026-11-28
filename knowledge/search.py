"""Searching the documentation by meaning (Phase 5, Step 4).

The question gets an embedding too. The chunks whose embeddings point most
the same way as the question's are the ones most likely to answer it, in
any language. No answer is written here: that is Step 5, from these chunks.
"""

import re

from dataclasses import dataclass
from itertools import zip_longest
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


SPLIT_FULL_SHARE = 3  # 6I-3: the whole question's best chunks come first
SPLIT_EXTRA = 3  # a split question may get this many more chunks than `limit`


def search_chunks(question, limit=5):
    """Return the chunks closest in meaning to the question, best first.

    A question about one thing gets `limit` chunks, as before. A question
    about two or more things (see split_question) also searches each part,
    so every part gets its own chunks, and up to `limit + SPLIT_EXTRA` come
    back (6I). Only chunks embedded by the current model are searched:
    embeddings from two models cannot be compared.
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
    parts = split_question(question)
    # One embedding call for the question and all its parts (6I-1).
    vectors, _ = embed_texts([question] + parts, client)
    if not parts:
        return closest_chunks(vectors[0], limit)
    found = [closest_chunks(vector, limit) for vector in vectors]
    return merge_results(found, first=SPLIT_FULL_SHARE, total=limit + SPLIT_EXTRA)


def closest_chunks(vector, limit):
    """The `limit` chunks whose embeddings are closest to `vector`, best first."""
    chunks = (
        DocChunk.objects.filter(
            embedding__isnull=False, embedding_model=settings.EMBEDDING_MODEL
        )
        .select_related("page")
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:limit]
    )
    # The database gives the distance (0 = same direction); 1 - distance is
    # the similarity, the same number as in the probe and the order check.
    return [SearchResult(chunk=chunk, score=1 - chunk.distance) for chunk in chunks]


def merge_results(found, first, total):
    """Mix the result lists of the whole question and of its parts (6I-3).

    found[0] is the whole question's list, the rest are the parts'. First come
    the whole question's best `first`, then the parts take turns, so no part
    can crowd another out; any room left goes to the rest of the whole
    question's list. No chunk twice, at most `total`.
    """
    merged, seen = [], set()

    def add(result):
        if result.chunk.pk not in seen and len(merged) < total:
            seen.add(result.chunk.pk)
            merged.append(result)

    for result in found[0][:first]:
        add(result)
    for one_round in zip_longest(*found[1:]):
        for result in one_round:
            if result is not None:
                add(result)
    for result in found[0][first:]:
        add(result)
    return merged


# 6I-2: words that join two questions in one ("A and B").
JOINING_WORDS = re.compile(r"\s+(?:আর|এবং|and)\s+", re.IGNORECASE)
LEADING_JOIN = re.compile(r"^(?:আর|এবং|and)\s+", re.IGNORECASE)
MIN_PART_WORDS = 2
MAX_PARTS = 3


def split_question(question):
    """Split a question that asks about two or more things into its parts.

    Returns [] when it asks about one thing. The rules are careful on purpose:
    a missed split keeps the old search, and a wrong one only adds a few
    excerpts, because the whole question is always searched too (6I-1).
    """
    parts = [part.strip() for part in question.split("?") if part.strip()]
    if len(parts) < 2:
        parts = [part.strip() for part in JOINING_WORDS.split(question) if part.strip()]
    parts = [LEADING_JOIN.sub("", part) for part in parts]
    if len(parts) < 2 or any(len(part.split()) < MIN_PART_WORDS for part in parts):
        return []
    if len(parts) > MAX_PARTS:
        parts = parts[: MAX_PARTS - 1] + [" ".join(parts[MAX_PARTS - 1 :])]
    return parts
