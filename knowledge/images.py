"""Describing documentation images (screenshots and diagrams) with the AI.

Phase 5, Step 2, part F. Some facts in the documentation are only in
pictures: which fields a form has, which buttons a list offers, how a
process flows. The AI looks at each image once and writes what it sees as
text, so it can be searched like the rest of the page.
"""

import time

import requests
from django.conf import settings
from openai import RateLimitError

from tickets.models import AILog
from tickets.triage import OpenAICompatibleClient

from .ingest import REQUEST_TIMEOUT, USER_AGENT
from .models import DocPage, ImageDescription

IMAGE_INSTRUCTIONS = (
    "You describe images from the user documentation of PrismERP, a business "
    "software. Most images are screenshots of one screen; some are diagrams.\n"
    "Write plain text in English, at most 200 words, without markdown:\n"
    "- First line: what the image shows (which screen, or which process).\n"
    "- Then the visible field names, column names, buttons and tabs. For a "
    "diagram, the steps in order.\n"
    "Write only what is visible. Do not guess or invent features. Leave out "
    "sample data such as names, amounts and dates. If the image has nothing "
    "useful (a logo, a decoration), write only: No useful content."
)
IMAGE_TIMEOUT = 60  # seconds; reading an image takes longer than a ticket
IMAGE_PAUSE_SECONDS = 5.0  # wait between images: stays under the free limit


def get_image_client():
    """The AI client for images, set up from .env, or None if not set up."""
    if not (settings.AI_BASE_URL and settings.AI_API_KEY and settings.AI_MODEL):
        return None
    return OpenAICompatibleClient(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
        timeout=IMAGE_TIMEOUT,
    )


def fetch_image(url):
    """Download one image. Return its bytes and its type, like "image/png"."""
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    # "image/png; charset=..." -> "image/png"
    mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    if not mime_type.startswith("image/"):
        raise ValueError(f"not an image: {mime_type or 'unknown type'}")
    return response.content, mime_type


def describe_and_save(url, client):
    """Have the AI describe one image and save it. Return the description.

    Errors are not caught here; the caller decides what to do. Every AI call
    is written to AILog, as in Phase 4. An empty answer is saved nowhere,
    so the image is tried again next time.
    """
    image_bytes, mime_type = fetch_image(url)
    log = AILog(purpose=AILog.Purpose.IMAGE_DESCRIPTION)
    started = time.monotonic()
    try:
        response = client.describe_image(IMAGE_INSTRUCTIONS, image_bytes, mime_type)
    except Exception as error:
        log.latency_ms = round((time.monotonic() - started) * 1000)
        log.status = AILog.Status.ERROR
        log.error_message = f"{url}: {type(error).__name__}: {error}"
        log.save()
        raise
    log.latency_ms = round((time.monotonic() - started) * 1000)
    log.provider = response.provider
    log.model_name = response.model_name
    log.input_tokens = response.input_tokens
    log.output_tokens = response.output_tokens
    log.raw_response = response.text

    description = response.text.strip()
    if not description:
        log.status = AILog.Status.INVALID
        log.error_message = f"{url}: empty answer"
        log.save()
        raise ValueError("the AI gave an empty answer")

    log.status = AILog.Status.SUCCESS
    log.save()
    ImageDescription.objects.create(
        url=url, description=description, model_name=response.model_name
    )
    return description


def describe_new_images(limit=20, pause=IMAGE_PAUSE_SECONDS, report=print):
    """Describe images that have no description yet, at most `limit` of them.

    Returns how many were described, how many failed, and how many are
    still left for a later run. Stops early when the AI's free limit is used.
    """
    client = get_image_client()
    if client is None:
        raise RuntimeError(
            "AI is not set up: fill AI_BASE_URL, AI_API_KEY and AI_MODEL in .env"
        )

    all_urls = {
        url
        for urls in DocPage.objects.values_list("image_urls", flat=True)
        for url in urls
    }
    done = set(ImageDescription.objects.values_list("url", flat=True))
    waiting = sorted(all_urls - done)

    counts = {"described": 0, "failed": 0, "left": 0}
    for url in waiting[:limit]:
        try:
            describe_and_save(url, client)
        except RateLimitError:
            report("stopped: the AI's free limit is used up, try again later")
            break
        # Broad on purpose: one bad image (download error, AI error) must not
        # stop the others. The AI's errors are already in AILog.
        except Exception as error:
            counts["failed"] += 1
            report(f"failed: {url} ({type(error).__name__}: {error})")
        else:
            counts["described"] += 1
            report(f"described: {url}")
        time.sleep(pause)
    counts["left"] = len(waiting) - counts["described"]
    return counts
