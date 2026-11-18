"""Embeddings for documentation chunks (Phase 5, Step 3).

An embedding is the meaning of a text as a list of numbers. Chunks with a
similar meaning get similar numbers, so a question can find the chunks that
answer it, even when it is asked in another language (Step 4).
"""

import time

from django.conf import settings
from django.db.models import Q
from openai import RateLimitError

from tickets.models import AILog
from tickets.triage import OpenAICompatibleClient

from .models import EMBEDDING_DIMENSIONS, DocChunk

BATCH_SIZE = 20  # chunks per AI call (the probe showed one call can take many)
EMBEDDING_TIMEOUT = 30  # seconds
EMBEDDING_PAUSE_SECONDS = 1.0  # wait between calls: stays under the free limit


def get_embedding_client():
    """The AI client for embeddings, set up from .env, or None if not set up."""
    if not (settings.AI_BASE_URL and settings.AI_API_KEY and settings.EMBEDDING_MODEL):
        return None
    return OpenAICompatibleClient(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.EMBEDDING_MODEL,
        timeout=EMBEDDING_TIMEOUT,
    )


def chunk_text(chunk):
    """The text sent for one chunk. The page title and heading add context:
    "Add Purchase Delivery" alone does not say it is about Purchase Delivery."""
    heading = f" > {chunk.heading}" if chunk.heading else ""
    return f"{chunk.page.title}{heading}\n{chunk.text}"


def embed_texts(texts, client):
    """Return (one embedding per text, model name). Logged in AILog.

    Errors are not caught here; the caller decides what to do. An answer
    with the wrong number or size of embeddings is refused, so a chunk can
    never get another chunk's meaning, or a size the database cannot keep.
    """
    log = AILog(purpose=AILog.Purpose.EMBEDDING)
    started = time.monotonic()
    try:
        response = client.embed(texts, settings.EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
    except Exception as error:
        log.latency_ms = round((time.monotonic() - started) * 1000)
        log.status = AILog.Status.ERROR
        log.error_message = f"{len(texts)} texts: {type(error).__name__}: {error}"
        log.save()
        raise
    log.latency_ms = round((time.monotonic() - started) * 1000)
    log.provider = response.provider
    log.model_name = response.model_name
    log.input_tokens = response.input_tokens
    # The numbers themselves are far too long to keep in the log.
    log.raw_response = f"{len(response.vectors)} embeddings"

    sizes = [len(vector) for vector in response.vectors]
    if sizes != [EMBEDDING_DIMENSIONS] * len(texts):
        log.status = AILog.Status.INVALID
        log.error_message = (
            f"asked for {len(texts)} embeddings of {EMBEDDING_DIMENSIONS} "
            f"numbers, got sizes {sizes}"
        )
        log.save()
        raise ValueError("the AI gave the wrong number or size of embeddings")

    log.status = AILog.Status.SUCCESS
    log.save()
    return response.vectors, response.model_name


def chunks_needing_embeddings():
    """Chunks with no embedding yet, or one made by a different model.

    Embeddings from two models cannot be compared, so after a model change
    every chunk is done again.
    """
    return (
        DocChunk.objects.filter(
            Q(embedding__isnull=True) | ~Q(embedding_model=settings.EMBEDDING_MODEL)
        )
        .select_related("page")
        .order_by("page__url", "position")
    )


def embed_new_chunks(limit=200, pause=EMBEDDING_PAUSE_SECONDS, report=print):
    """Embed up to `limit` chunks that need it, BATCH_SIZE chunks per call.

    Returns how many were embedded, how many failed, and how many still need
    an embedding. Stops early when the AI's free limit is used up.
    """
    client = get_embedding_client()
    if client is None:
        raise RuntimeError(
            "AI is not set up: fill AI_BASE_URL, AI_API_KEY and EMBEDDING_MODEL "
            "in .env"
        )

    waiting_total = chunks_needing_embeddings().count()
    waiting = list(chunks_needing_embeddings()[:limit])
    counts = {"embedded": 0, "failed": 0, "left": 0}
    for start in range(0, len(waiting), BATCH_SIZE):
        batch = waiting[start : start + BATCH_SIZE]
        try:
            vectors, model_name = embed_texts([chunk_text(c) for c in batch], client)
        except RateLimitError:
            report("stopped: the AI's free limit is used up, try again later")
            break
        # Broad on purpose: one failed call must not stop the other batches.
        # The error is already in AILog.
        except Exception as error:
            counts["failed"] += len(batch)
            report(f"failed: {len(batch)} chunks ({type(error).__name__}: {error})")
        else:
            for chunk, vector in zip(batch, vectors):
                chunk.embedding = vector
                chunk.embedding_model = model_name
            DocChunk.objects.bulk_update(batch, ["embedding", "embedding_model"])
            counts["embedded"] += len(batch)
            report(f"embedded: {len(batch)} chunks")
        time.sleep(pause)
    counts["left"] = waiting_total - counts["embedded"]
    return counts
