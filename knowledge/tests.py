"""Tests for the knowledge base models, including a real vector search."""

import csv
import json
import tempfile

from io import StringIO
from pathlib import Path
from unittest import mock

import requests
from django.core.management import CommandError, call_command
from django.core.cache import cache

from django.db import DataError, IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from pgvector.django import CosineDistance
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from accounts.models import User
from tickets.models import AILog
from tickets.triage import AIResponse, EmbeddingResponse, FakeAIClient

from .answer import (
    NOT_FOUND_TEXT,
    AnswerValidationError,
    answer_question,
    ANSWER_TEMPERATURE,
    build_answer_prompt,
    Answer,
    ForeignScriptError,
    foreign_letters,
    parse_answer,
    get_answer_client,
)
from .embeddings import chunk_text, embed_new_chunks
from .images import describe_new_images, fetch_image
from .management.commands.evaluate_answers import QUESTIONS_FILE, check
from .ingest import (
    PageContent,
    Section,
    add_all_image_chunks,
    add_image_chunks,
    crawl,
    extract_page,
    module_from_url,
    save_page,
    split_into_chunks,
)
from .models import (
    EMBEDDING_DIMENSIONS,
    DocChunk,
    DocPage,
    ImageDescription,
    ModuleOverview,
)
from .overview import OverviewValidationError, build_overviews, parse_overview
from .search import SearchResult, search_chunks
from .views import UNAVAILABLE


def vector(*start):
    """A full-size embedding for tests: the given numbers, then zeros."""
    return list(start) + [0.0] * (EMBEDDING_DIMENSIONS - len(start))


class DocModelTests(TestCase):
    """Pages, chunks and their embeddings in the database (with pgvector)."""

    def setUp(self):
        self.page = DocPage.objects.create(
            url="https://docs.example.com/supply-chain/purchase-order/",
            module="supply-chain",
            title="Purchase Order",
            content="How to create a purchase order.",
            content_hash="abc",
        )

    def make_chunk(self, position, embedding=None):
        return DocChunk.objects.create(
            page=self.page, position=position, text="Part", embedding=embedding
        )

    def test_chunks_come_back_in_page_order(self):
        self.make_chunk(1)
        self.make_chunk(0)
        self.assertEqual([c.position for c in self.page.chunks.all()], [0, 1])

    def test_deleting_a_page_deletes_its_chunks(self):
        self.make_chunk(0)
        self.page.delete()
        self.assertEqual(DocChunk.objects.count(), 0)

    def test_two_chunks_cannot_share_a_position(self):
        self.make_chunk(0)
        with self.assertRaises(IntegrityError):
            self.make_chunk(0)

    def test_embedding_is_stored_and_read_back(self):
        chunk = self.make_chunk(0, embedding=vector(0.5, 0.25, 1.0))
        chunk.refresh_from_db()
        self.assertEqual(list(chunk.embedding), vector(0.5, 0.25, 1.0))

    # A first taste of Step 4: the database sorts chunks by closeness in meaning.
    def test_nearest_chunk_comes_first(self):
        self.make_chunk(0, embedding=vector(0.0, 1.0))  # points another way: far
        self.make_chunk(1, embedding=vector(1.0, 0.0))  # same direction as the question
        self.make_chunk(2, embedding=vector(0.9, 0.1))  # almost the same direction
        question = vector(1.0, 0.0)
        nearest = DocChunk.objects.order_by(CosineDistance("embedding", question))
        self.assertEqual([c.position for c in nearest], [1, 2, 0])

    def test_embedding_of_the_wrong_size_is_refused(self):
        with self.assertRaises(DataError), transaction.atomic():
            self.make_chunk(0, embedding=[0.5, 0.25, 1.0])


SAMPLE_PAGE = """
<html><head><title>Purchase Order | PrismERP</title></head><body>
<nav class="navbar">Home Site menu</nav>
<main><article>
  <nav class="theme-doc-breadcrumbs">Supply Chain / Purchase Order</nav>
  <header><h1>Purchase Order (PO)</h1></header>
  <p>A purchase order confirms an order to a supplier.</p>
  <h2>New Purchase Order<a class="hash-link" href="#new">#</a></h2>
  <p><img src="/img/po-form.png" alt="New PO form"></p>
  <table>
    <thead><tr><th>Field</th><th>Description</th></tr></thead>
    <tbody>
      <tr><td>Order Date</td><td>The date the order is placed.</td></tr>
      <tr><td>Supplier</td><td><p>The supplier who will deliver.</p></td></tr>
    </tbody>
  </table>
  <h3>Actions</h3>
  <ul>
    <li><p>Approve the order.</p></li>
    <li>Cancel the order.</li>
  </ul>
  <footer class="theme-doc-footer">Edit this page</footer>
</article></main>
<footer class="footer">Copyright Site</footer>
</body></html>
"""
PAGE_URL = "https://docs.example.com/supply-chain/purchase-order/"


class ExtractPageTests(SimpleTestCase):
    """extract_page on a small hand-written page shaped like a Docusaurus page."""

    def setUp(self):
        self.page = extract_page(SAMPLE_PAGE, PAGE_URL)
        self.all_text = " ".join(section.text for section in self.page.sections)

    def test_title_comes_from_h1(self):
        self.assertEqual(self.page.title, "Purchase Order (PO)")

    def test_text_is_split_by_headings(self):
        headings = [section.heading for section in self.page.sections]
        self.assertEqual(headings, ["", "New Purchase Order", "Actions"])

    def test_table_rows_become_field_lines(self):
        self.assertEqual(
            self.page.sections[1].text,
            "Order Date: The date the order is placed.\n"
            "Supplier: The supplier who will deliver.",
        )

    def test_list_items_are_kept_once(self):
        self.assertEqual(
            self.page.sections[2].text, "- Approve the order.\n- Cancel the order."
        )

    def test_site_parts_are_dropped(self):
        for site_text in ["Site menu", "Supply Chain /", "Edit this page", "Copyright"]:
            self.assertNotIn(site_text, self.all_text)
        self.assertNotIn("#", self.page.sections[1].heading)

    def test_image_addresses_are_made_full(self):
        self.assertEqual(
            self.page.image_urls, ["https://docs.example.com/img/po-form.png"]
        )


class SavePageTests(TestCase):
    """Chunking and saving pages: created, unchanged and updated pages."""

    def save(self, html=SAMPLE_PAGE):
        return save_page(PAGE_URL, extract_page(html, PAGE_URL))

    def test_module_comes_from_the_address(self):
        self.assertEqual(module_from_url(PAGE_URL), "supply-chain")
        self.assertEqual(module_from_url("https://docs.example.com/"), "")

    def test_long_section_is_cut_between_lines(self):
        line = " ".join(["word"] * 100)  # one line of 100 words
        section = Section("Fields", "\n".join([line] * 4))  # 400 words
        chunks = split_into_chunks([section], max_words=300)
        self.assertEqual([len(text.split()) for _, text in chunks], [300, 100])
        self.assertEqual([heading for heading, _ in chunks], ["Fields", "Fields"])

    def test_new_page_is_created_with_its_chunks(self):
        self.assertEqual(self.save(), "created")
        page = DocPage.objects.get(url=PAGE_URL)
        self.assertEqual(page.module, "supply-chain")
        self.assertEqual(page.title, "Purchase Order (PO)")
        self.assertEqual(page.image_urls, ["https://docs.example.com/img/po-form.png"])
        headings = [chunk.heading for chunk in page.chunks.all()]
        self.assertEqual(headings, ["", "New Purchase Order", "Actions"])

    def test_same_page_again_changes_nothing_and_keeps_embeddings(self):
        self.save()
        chunk = DocChunk.objects.first()
        chunk.embedding = vector(1.0, 0.0)
        chunk.save()

        self.assertEqual(self.save(), "unchanged")
        chunk.refresh_from_db()  # still there, with its embedding
        self.assertEqual(list(chunk.embedding), vector(1.0, 0.0))

    def test_changed_page_gets_new_chunks(self):
        self.save()
        DocChunk.objects.update(embedding=vector(1.0, 0.0))
        changed = SAMPLE_PAGE.replace("Cancel the order.", "Close the order.")

        self.assertEqual(self.save(changed), "updated")
        self.assertEqual(DocPage.objects.count(), 1)  # same page, not a second one
        texts = [chunk.text for chunk in DocChunk.objects.all()]
        self.assertIn("- Approve the order.\n- Close the order.", texts)
        # New chunks have no embedding yet: the old ones described old text.
        self.assertFalse(DocChunk.objects.filter(embedding__isnull=False).exists())


# A pretend website for the crawl tests: address -> HTML. No internet needed.
START = "https://docs.example.com/guide/"
FAKE_SITE = {
    START: """<article><h1>Guide</h1><p>Start page.</p>
        <a href="/guide/a/">A</a>
        <a href="/guide/b/#part">B, a spot inside the page</a>
        <a href="/other/x/">Another section</a>
        <a href="https://elsewhere.com/guide/">Another website</a></article>""",
    START + "a/": """<article><h1>A</h1><p>Page A.</p>
        <a href="/guide/">Back to the start</a>
        <a href="/guide/b/">B again</a></article>""",
    START + "b/": """<article><h1>B</h1><p>Page B.</p>
        <a href="/guide/c/">C</a></article>""",
    START + "c/": "<article><h1>C</h1><p>Page C.</p></article>",
}


class CrawlTests(SimpleTestCase):
    """crawl with a fake website, fake robots.txt and a fake save_page.

    save_page has its own tests above, so here it only records the call;
    that also means these tests need no database.
    """

    def run_crawl(self, start=START, limit=50, blocked=(), broken=()):
        """Run crawl; return (counts, fetched addresses, saved addresses)."""
        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            if url in broken:
                raise requests.ConnectionError("network down")
            return FAKE_SITE[url]

        with (
            mock.patch("knowledge.ingest.fetch_html", side_effect=fake_fetch),
            mock.patch("knowledge.ingest.robotparser.RobotFileParser") as robots,
            mock.patch("knowledge.ingest.save_page", return_value="created") as save,
        ):
            robots.return_value.can_fetch.side_effect = (
                lambda agent, url: url not in blocked
            )
            counts = crawl(start, limit=limit, pause=0, report=lambda message: None)
        saved = [call.args[0] for call in save.call_args_list]
        return counts, fetched, saved

    def test_follows_links_inside_the_section_once_each(self):
        counts, fetched, saved = self.run_crawl()
        self.assertEqual(fetched, [START, START + "a/", START + "b/", START + "c/"])
        self.assertEqual(saved, fetched)
        self.assertEqual(counts["created"], 4)

    def test_stops_at_the_limit(self):
        counts, fetched, saved = self.run_crawl(limit=2)
        self.assertEqual(fetched, [START, START + "a/"])
        self.assertEqual(counts["created"], 2)

    def test_skips_pages_that_robots_txt_forbids(self):
        counts, fetched, saved = self.run_crawl(blocked={START + "b/"})
        self.assertNotIn(START + "b/", fetched)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["created"], 2)  # C is only linked from B

    def test_a_failed_page_does_not_stop_the_crawl(self):
        counts, fetched, saved = self.run_crawl(broken={START + "a/"})
        self.assertEqual(counts["failed"], 1)
        self.assertNotIn(START + "a/", saved)
        self.assertIn(START + "c/", saved)

    def test_adds_the_missing_slash_to_the_start_address(self):
        counts, fetched, saved = self.run_crawl(start=START.rstrip("/"))
        self.assertEqual(fetched[0], START)
        self.assertEqual(counts["created"], 4)


class FetchDocsCommandTests(SimpleTestCase):
    def test_passes_the_address_and_limit_and_prints_the_totals(self):
        totals = {"created": 3, "updated": 0, "unchanged": 1}
        out = StringIO()
        with mock.patch(
            "knowledge.management.commands.fetch_docs.crawl", return_value=totals
        ) as fake_crawl:
            call_command("fetch_docs", START, "--limit", "5", stdout=out)
        self.assertEqual(fake_crawl.call_args.args, (START,))
        self.assertEqual(fake_crawl.call_args.kwargs["limit"], 5)
        self.assertIn("Done: created 3, updated 0, unchanged 1", out.getvalue())


# Image addresses for the image tests.
IMG = "https://docs.example.com/assets/images/"


class DescribeImagesTests(TestCase):
    """describe_new_images with a fake AI and fake downloads: no network, no cost."""

    def setUp(self):
        # two.png is on both pages; one.png was described in an earlier run.
        pages = {"a": ["one.png", "two.png"], "b": ["two.png", "three.png"]}
        for name, images in pages.items():
            DocPage.objects.create(
                url=f"https://docs.example.com/guide/{name}/",
                module="guide",
                title=name,
                content="Text.",
                content_hash=name,
                image_urls=[IMG + image for image in images],
            )
        ImageDescription.objects.create(
            url=IMG + "one.png", description="Old.", model_name="old-model"
        )

    def run_describe(self, client, limit=20):
        with (
            mock.patch("knowledge.images.get_image_client", return_value=client),
            mock.patch(
                "knowledge.images.fetch_image", return_value=(b"\x89PNG", "image/png")
            ),
        ):
            return describe_new_images(
                limit=limit, pause=0, report=lambda message: None
            )

    def test_describes_each_new_image_once(self):
        client = FakeAIClient(text="  Purchase Order form with Vendor field.  ")
        counts = self.run_describe(client)
        self.assertEqual(counts, {"described": 2, "failed": 0, "left": 0})
        self.assertEqual(len(client.calls), 2)  # two.png is read only once
        saved = ImageDescription.objects.get(url=IMG + "two.png")
        self.assertEqual(saved.description, "Purchase Order form with Vendor field.")
        self.assertEqual(saved.model_name, "fake-model")
        logs = AILog.objects.filter(
            purpose=AILog.Purpose.IMAGE_DESCRIPTION, status=AILog.Status.SUCCESS
        )
        self.assertEqual(logs.count(), 2)

    def test_stops_at_the_limit(self):
        counts = self.run_describe(FakeAIClient(text="A form."), limit=1)
        self.assertEqual(counts, {"described": 1, "failed": 0, "left": 1})

    def test_ai_error_is_logged_and_the_image_waits_for_next_time(self):
        counts = self.run_describe(FakeAIClient(error=TimeoutError("too slow")))
        self.assertEqual(counts, {"described": 0, "failed": 2, "left": 2})
        self.assertEqual(ImageDescription.objects.count(), 1)  # only the old one
        log = AILog.objects.filter(status=AILog.Status.ERROR).first()
        self.assertIn("TimeoutError", log.error_message)
        self.assertIn(IMG, log.error_message)  # says which image failed

    def test_empty_answer_is_invalid_and_not_saved(self):
        counts = self.run_describe(FakeAIClient(text="   "))
        self.assertEqual(counts["failed"], 2)
        self.assertEqual(AILog.objects.filter(status=AILog.Status.INVALID).count(), 2)
        self.assertEqual(ImageDescription.objects.count(), 1)

    def test_free_limit_stops_the_run(self):
        class FakeLimit(Exception):
            pass

        client = FakeAIClient(error=FakeLimit("429"))
        with mock.patch("knowledge.images.RateLimitError", FakeLimit):
            counts = self.run_describe(client)
        self.assertEqual(len(client.calls), 1)  # did not try the second image
        self.assertEqual(counts, {"described": 0, "failed": 0, "left": 2})

    def test_without_ai_settings_it_refuses_to_run(self):
        with self.assertRaises(RuntimeError):
            self.run_describe(None)


class FetchImageTests(SimpleTestCase):
    def test_returns_the_bytes_and_the_type(self):
        fake = mock.Mock(headers={"Content-Type": "image/png"}, content=b"\x89PNG")
        with mock.patch("knowledge.images.requests.get", return_value=fake):
            self.assertEqual(fetch_image(IMG + "one.png"), (b"\x89PNG", "image/png"))

    def test_refuses_something_that_is_not_an_image(self):
        fake = mock.Mock(headers={"Content-Type": "text/html; charset=utf-8"})
        with mock.patch("knowledge.images.requests.get", return_value=fake):
            with self.assertRaises(ValueError):
                fetch_image(IMG + "missing.png")


class DescribeImagesCommandTests(SimpleTestCase):
    TARGET = "knowledge.management.commands.describe_images.describe_new_images"

    def test_passes_the_limit_and_prints_the_totals(self):
        totals = {"described": 2, "failed": 0, "left": 5}
        out = StringIO()
        with (
            mock.patch(self.TARGET, return_value=totals) as fake,
            mock.patch(
                "knowledge.management.commands.describe_images.add_all_image_chunks",
                return_value=4,
            ),
        ):
            call_command("describe_images", "--limit", "2", stdout=out)
        self.assertEqual(fake.call_args.kwargs["limit"], 2)
        self.assertIn(
            "Done: described 2, failed 0, left 5, image chunks 4", out.getvalue()
        )

    def test_missing_ai_settings_give_a_clear_error(self):
        with mock.patch(self.TARGET, side_effect=RuntimeError("AI is not set up")):
            with self.assertRaises(CommandError):
                call_command("describe_images")


class ImageChunkTests(TestCase):
    """Image descriptions become chunks after the page's text chunks."""

    def setUp(self):
        self.page = DocPage.objects.create(
            url="https://docs.example.com/guide/po/",
            module="guide",
            title="Purchase Order",
            content="Text.",
            content_hash="abc",
            image_urls=[IMG + "one.png", IMG + "two.png", IMG + "three.png"],
        )
        for position in (0, 1):
            DocChunk.objects.create(page=self.page, position=position, text="Text.")
        ImageDescription.objects.create(
            url=IMG + "one.png",
            description="Purchase Order screen.\nFields: Vendor, Branch.",
            model_name="fake-model",
        )
        ImageDescription.objects.create(
            url=IMG + "two.png", description="No useful content.", model_name="m"
        )
        # three.png has no description yet.

    def image_chunks(self):
        return list(self.page.chunks.exclude(image_url=""))

    def test_useful_descriptions_become_chunks_after_the_text(self):
        self.assertEqual(add_image_chunks(self.page), 1)  # two.png is useless
        [chunk] = self.image_chunks()
        self.assertEqual(chunk.position, 2)
        self.assertEqual(chunk.image_url, IMG + "one.png")
        self.assertEqual(chunk.heading, "Image: Purchase Order screen.")
        self.assertIn('page "Purchase Order"', chunk.text)
        self.assertIn("Fields: Vendor, Branch.", chunk.text)

    def test_running_again_adds_nothing(self):
        add_image_chunks(self.page)
        self.assertEqual(add_image_chunks(self.page), 0)
        self.assertEqual(self.page.chunks.count(), 3)

    def test_a_later_description_is_added_at_the_end(self):
        add_image_chunks(self.page)
        ImageDescription.objects.create(
            url=IMG + "three.png", description="A diagram.", model_name="m"
        )
        self.assertEqual(add_all_image_chunks(), 1)
        self.assertEqual([c.position for c in self.image_chunks()], [2, 3])

    def test_text_chunks_keep_their_embedding(self):
        chunk = self.page.chunks.get(position=0)
        chunk.embedding = vector(0.5, 0.25, 1.0)
        chunk.save()
        add_image_chunks(self.page)
        chunk.refresh_from_db()
        self.assertEqual(list(chunk.embedding), vector(0.5, 0.25, 1.0))

    def test_a_changed_page_gets_its_image_chunks_back(self):
        new_version = PageContent(
            title="Purchase Order",
            sections=[Section("", "New text.")],
            image_urls=[IMG + "one.png"],
        )
        self.assertEqual(save_page(self.page.url, new_version), "updated")
        chunks = list(self.page.chunks.all())
        self.assertEqual([c.image_url for c in chunks], ["", IMG + "one.png"])


@override_settings(EMBEDDING_MODEL="test-embed")
class EmbedChunksTests(TestCase):
    """embed_new_chunks with a fake AI: no network, no cost."""

    def setUp(self):
        self.page = DocPage.objects.create(
            url="https://docs.example.com/guide/po/",
            module="guide",
            title="Purchase Order",
            content="Text.",
            content_hash="abc",
        )
        # 25 chunks: one full batch of 20, then 5. Odd ones have a heading.
        for position in range(25):
            DocChunk.objects.create(
                page=self.page,
                position=position,
                heading="Fields" if position % 2 else "",
                text=f"Text {position}",
            )

    def run_embed(self, client, limit=200):
        with mock.patch(
            "knowledge.embeddings.get_embedding_client", return_value=client
        ):
            return embed_new_chunks(limit=limit, pause=0, report=lambda message: None)

    def test_embeds_every_chunk_in_batches(self):
        client = FakeAIClient()
        counts = self.run_embed(client)
        self.assertEqual(counts, {"embedded": 25, "failed": 0, "left": 0})
        self.assertEqual([len(texts) for _, texts in client.calls], [20, 5])
        self.assertFalse(DocChunk.objects.filter(embedding__isnull=True).exists())
        self.assertEqual(
            set(DocChunk.objects.values_list("embedding_model", flat=True)),
            {"test-embed"},
        )
        logs = AILog.objects.filter(
            purpose=AILog.Purpose.EMBEDDING, status=AILog.Status.SUCCESS
        )
        self.assertEqual(logs.count(), 2)

    def test_text_sent_has_the_page_title_and_heading(self):
        chunks = self.page.chunks.all()
        self.assertEqual(chunk_text(chunks[0]), "Purchase Order\nText 0")
        self.assertEqual(chunk_text(chunks[1]), "Purchase Order > Fields\nText 1")

    def test_running_again_sends_nothing(self):
        self.run_embed(FakeAIClient())
        client = FakeAIClient()
        counts = self.run_embed(client)
        self.assertEqual(counts, {"embedded": 0, "failed": 0, "left": 0})
        self.assertEqual(client.calls, [])

    def test_chunks_from_another_model_are_done_again(self):
        self.run_embed(FakeAIClient())
        DocChunk.objects.update(embedding_model="old-model")
        counts = self.run_embed(FakeAIClient())
        self.assertEqual(counts["embedded"], 25)

    def test_stops_at_the_limit(self):
        client = FakeAIClient()
        counts = self.run_embed(client, limit=5)
        self.assertEqual(counts, {"embedded": 5, "failed": 0, "left": 20})
        self.assertEqual([len(texts) for _, texts in client.calls], [5])

    def test_wrong_number_of_embeddings_is_refused(self):
        client = mock.Mock()
        # Only one embedding, whatever the number of texts sent.
        client.embed.return_value = EmbeddingResponse(
            vectors=[vector(1.0)], provider="fake", model_name="test-embed"
        )
        counts = self.run_embed(client)
        self.assertEqual(counts, {"embedded": 0, "failed": 25, "left": 25})
        self.assertEqual(AILog.objects.filter(status=AILog.Status.INVALID).count(), 2)
        self.assertFalse(DocChunk.objects.filter(embedding__isnull=False).exists())

    def test_ai_error_leaves_the_chunks_for_next_time(self):
        counts = self.run_embed(FakeAIClient(error=TimeoutError("too slow")))
        self.assertEqual(counts, {"embedded": 0, "failed": 25, "left": 25})
        log = AILog.objects.filter(status=AILog.Status.ERROR).first()
        self.assertIn("TimeoutError", log.error_message)

    def test_free_limit_stops_the_run(self):
        class FakeLimit(Exception):
            pass

        client = FakeAIClient(error=FakeLimit("429"))
        with mock.patch("knowledge.embeddings.RateLimitError", FakeLimit):
            counts = self.run_embed(client)
        self.assertEqual(len(client.calls), 1)  # did not try the second batch
        self.assertEqual(counts, {"embedded": 0, "failed": 0, "left": 25})

    def test_without_ai_settings_it_refuses_to_run(self):
        with self.assertRaises(RuntimeError):
            self.run_embed(None)


class EmbedChunksCommandTests(SimpleTestCase):
    TARGET = "knowledge.management.commands.embed_chunks.embed_new_chunks"

    def test_passes_the_limit_and_prints_the_totals(self):
        totals = {"embedded": 20, "failed": 0, "left": 93}
        out = StringIO()
        with mock.patch(self.TARGET, return_value=totals) as fake:
            call_command("embed_chunks", "--limit", "20", stdout=out)
        self.assertEqual(fake.call_args.kwargs["limit"], 20)
        self.assertIn("Done: embedded 20, failed 0, left 93", out.getvalue())

    def test_missing_ai_settings_give_a_clear_error(self):
        with mock.patch(self.TARGET, side_effect=RuntimeError("AI is not set up")):
            with self.assertRaises(CommandError):
                call_command("embed_chunks")


@override_settings(EMBEDDING_MODEL="test-embed")
class SearchChunksTests(TestCase):
    """search_chunks with a fake question embedding: no network, no cost."""

    def setUp(self):
        page = DocPage.objects.create(
            url="https://docs.example.com/guide/po/",
            module="guide",
            title="Purchase Order",
            content="Text.",
            content_hash="abc",
        )

        def chunk(position, text, embedding=None, model="test-embed"):
            return DocChunk.objects.create(
                page=page,
                position=position,
                text=text,
                embedding=embedding,
                embedding_model=model if embedding else "",
            )

        # The question will point along the first number: vector(1.0).
        self.far = chunk(0, "Far", vector(0.0, 1.0))  # another way
        self.same = chunk(1, "Same", vector(1.0, 0.0))  # the same way
        self.close = chunk(2, "Close", vector(0.9, 0.1))  # almost the same way
        # Must be left out: made by another model, or not embedded yet.
        chunk(3, "Old model", vector(1.0, 0.0), model="old-model")
        chunk(4, "Not embedded")

    def fake_client(self):
        client = mock.Mock()
        client.embed.return_value = EmbeddingResponse(
            vectors=[vector(1.0)], provider="fake", model_name="test-embed"
        )
        return client

    def search(self, question="How do I make a purchase order?", limit=5):
        client = self.fake_client()
        with mock.patch("knowledge.search.get_embedding_client", return_value=client):
            return search_chunks(question, limit=limit), client

    def test_the_closest_chunk_comes_first(self):
        results, client = self.search()
        self.assertEqual(
            [result.chunk for result in results], [self.same, self.close, self.far]
        )
        self.assertAlmostEqual(results[0].score, 1.0, places=5)  # same meaning
        self.assertAlmostEqual(results[2].score, 0.0, places=5)  # unrelated

    def test_other_model_and_unembedded_chunks_are_left_out(self):
        results, client = self.search()
        texts = [result.chunk.text for result in results]
        self.assertNotIn("Old model", texts)
        self.assertNotIn("Not embedded", texts)

    def test_stops_at_the_limit(self):
        results, client = self.search(limit=2)
        self.assertEqual([result.chunk for result in results], [self.same, self.close])

    def test_the_question_is_trimmed_and_the_call_is_logged(self):
        results, client = self.search("   How?   ")
        self.assertEqual(client.embed.call_args.args[0], ["How?"])
        self.assertEqual(
            AILog.objects.filter(purpose=AILog.Purpose.EMBEDDING).count(), 1
        )

    def test_an_empty_or_too_long_question_is_refused_without_an_ai_call(self):
        client = self.fake_client()
        with mock.patch("knowledge.search.get_embedding_client", return_value=client):
            for question in ["   ", "a" * 1001]:
                with self.assertRaises(ValueError):
                    search_chunks(question)
        client.embed.assert_not_called()

    def test_without_ai_settings_it_refuses_to_run(self):
        with mock.patch("knowledge.search.get_embedding_client", return_value=None):
            with self.assertRaises(RuntimeError):
                search_chunks("How?")


class SearchDocsCommandTests(SimpleTestCase):
    TARGET = "knowledge.management.commands.search_docs.search_chunks"

    def test_prints_each_result_with_its_score_page_and_link(self):
        page = DocPage(url="https://docs.example.com/guide/po/", title="Purchase Order")
        chunk = DocChunk(
            page=page, heading="Actions", text="Approve the order.\nCancel the order."
        )
        found = [SearchResult(chunk=chunk, score=0.8123)]
        out = StringIO()
        with mock.patch(self.TARGET, return_value=found) as fake:
            call_command("search_docs", "How do I cancel?", "--limit", "3", stdout=out)
        self.assertEqual(fake.call_args.args, ("How do I cancel?",))
        self.assertEqual(fake.call_args.kwargs["limit"], 3)
        printed = out.getvalue()
        self.assertIn("1. 0.812  Purchase Order > Actions", printed)
        self.assertIn("https://docs.example.com/guide/po/", printed)
        self.assertIn("Approve the order. Cancel the order.", printed)

    def test_a_bad_question_gives_a_clear_error(self):
        with mock.patch(self.TARGET, side_effect=ValueError("the question is empty")):
            with self.assertRaises(CommandError):
                call_command("search_docs", " ")


class AnswerPromptTests(SimpleTestCase):
    """The prompt for answering: question and numbered excerpts, kept apart."""

    def setUp(self):
        page = DocPage(
            url="https://docs.example.com/guide/pc/", title="Purchase Cancellation"
        )
        results = [
            SearchResult(
                chunk=DocChunk(page=page, heading="New Order", text="Party: Select."),
                score=0.8,
            ),
            SearchResult(
                chunk=DocChunk(page=page, text="Cancel orders here."), score=0.7
            ),
        ]
        self.instructions, self.text = build_answer_prompt(
            "PO kivabe cancel korbo?", results
        )

    def test_the_question_is_inside_its_tags(self):
        self.assertIn("<question>\nPO kivabe cancel korbo?\n</question>", self.text)

    def test_excerpts_are_numbered_with_page_and_heading(self):
        self.assertIn(
            "[1] Purchase Cancellation > New Order\nParty: Select.", self.text
        )
        self.assertIn("[2] Purchase Cancellation\nCancel orders here.", self.text)

    def test_the_rules_ask_for_json_only_from_the_excerpts(self):
        self.assertIn('"found"', self.instructions)
        self.assertIn("ONLY the documentation excerpts", self.instructions)
        self.assertIn("a question in English: answer in English", self.instructions)
        self.assertIn("too vague to answer", self.instructions)
        # The customer's words never go into our instructions.
        self.assertNotIn("kivabe", self.instructions)
        self.assertIn("Use no other script", self.instructions)


def ai_says(found, answer="", sources=()):
    """A fake AI that gives this answer as JSON."""
    reply = {"found": found, "answer": answer, "sources": list(sources)}
    return FakeAIClient(text=json.dumps(reply))


class AnswerQuestionTests(TestCase):
    """answer_question with fake search results and a fake AI: no cost."""

    def setUp(self):
        cancel = DocPage(
            url="https://docs.example.com/guide/pc/", title="Purchase Cancellation"
        )
        order = DocPage(
            url="https://docs.example.com/guide/po/", title="Purchase Order"
        )
        self.results = [
            SearchResult(
                chunk=DocChunk(page=cancel, heading="New Order", text="Party: Select."),
                score=0.8,
            ),
            SearchResult(
                chunk=DocChunk(page=cancel, text="Cancel orders here."), score=0.7
            ),
            SearchResult(
                chunk=DocChunk(page=order, text="Make orders here."), score=0.6
            ),
        ]

    def ask(self, client, results=None):
        found = self.results if results is None else results
        with (
            mock.patch("knowledge.answer.search_chunks", return_value=found),
            mock.patch("knowledge.answer.get_answer_client", return_value=client),
        ):
            return answer_question("PO kivabe cancel korbo?")

    def answer_log(self):
        return AILog.objects.get(purpose=AILog.Purpose.ANSWER)

    def test_an_answer_comes_with_its_page_links(self):
        answer = self.ask(ai_says(True, "Open New Order.", [1, 2]))
        self.assertTrue(answer.found)
        self.assertEqual(answer.text, "Open New Order.")
        # Excerpts 1 and 2 are from the same page: one link.
        self.assertEqual(
            answer.sources,
            [
                {
                    "title": "Purchase Cancellation",
                    "url": "https://docs.example.com/guide/pc/",
                }
            ],
        )
        self.assertEqual(self.answer_log().status, AILog.Status.SUCCESS)

    def test_the_ai_gets_the_question_and_the_numbered_excerpts(self):
        client = ai_says(False)
        self.ask(client)
        instructions, text = client.calls[0]
        self.assertIn("PO kivabe cancel korbo?", text)
        self.assertIn("[3] Purchase Order\nMake orders here.", text)

    def test_not_found_gives_our_own_message(self):
        answer = self.ask(ai_says(False))
        self.assertFalse(answer.found)
        self.assertEqual(answer.text, NOT_FOUND_TEXT)
        self.assertEqual(answer.sources, [])

    def test_no_chunks_means_not_found_without_an_ai_call(self):
        client = ai_says(True, "unused", [1])
        answer = self.ask(client, results=[])
        self.assertFalse(answer.found)
        self.assertEqual(client.calls, [])

    def test_a_made_up_source_number_is_refused(self):
        with self.assertRaises(AnswerValidationError):
            self.ask(ai_says(True, "Yes.", [4]))  # there are only 3 excerpts
        self.assertEqual(self.answer_log().status, AILog.Status.INVALID)
        self.assertIn("4", self.answer_log().error_message)

    def test_an_answer_without_sources_is_refused(self):
        with self.assertRaises(AnswerValidationError):
            self.ask(ai_says(True, "Yes.", []))

    def test_json_inside_extra_words_is_still_read(self):
        client = FakeAIClient(
            text='Here it is:\n```json\n{"found": true, "answer": "Yes.", '
            '"sources": [3]}\n```'
        )
        answer = self.ask(client)
        self.assertEqual(answer.sources[0]["title"], "Purchase Order")

    def test_an_ai_error_is_logged_and_raised(self):
        with self.assertRaises(TimeoutError):
            self.ask(FakeAIClient(error=TimeoutError("too slow")))
        self.assertEqual(self.answer_log().status, AILog.Status.ERROR)

    def test_without_ai_settings_it_refuses_to_run(self):
        with self.assertRaises(RuntimeError):
            self.ask(None)

    @override_settings(
        AI_BASE_URL="https://example.com/v1/", AI_API_KEY="test-key", AI_MODEL="m"
    )
    def test_answers_use_a_low_temperature(self):
        self.assertEqual(get_answer_client().temperature, ANSWER_TEMPERATURE)


ASK_URL = "/api/v1/knowledge/ask/"


class AskApiTests(APITestCase):
    """POST /api/v1/knowledge/ask/ with a fake answer_question: no AI, no cost."""

    TARGET = "knowledge.views.answer_question"
    FOUND = Answer(
        found=True,
        text="Open New Order.",
        sources=[
            {
                "title": "Purchase Cancellation",
                "url": "https://docs.example.com/guide/pc/",
            }
        ],
    )

    def setUp(self):
        cache.clear()  # throttling counts requests in the cache: start from zero
        rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.client.force_authenticate(user=rahim)

    def ask(self, question):
        return self.client.post(ASK_URL, {"question": question})

    def test_an_answer_comes_back_with_its_sources(self):
        with mock.patch(self.TARGET, return_value=self.FOUND) as fake:
            response = self.ask("  PO kivabe cancel korbo?  ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "found": True,
                "answer": "Open New Order.",
                "sources": self.FOUND.sources,
            },
        )
        fake.assert_called_once_with("PO kivabe cancel korbo?")  # spaces trimmed

    def test_not_found_is_still_a_normal_answer(self):
        not_found = Answer(found=False, text=NOT_FOUND_TEXT)
        with mock.patch(self.TARGET, return_value=not_found):
            response = self.ask("How do I process employee salary?")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["found"])
        self.assertEqual(response.data["sources"], [])

    def test_login_is_required(self):
        self.client.force_authenticate(user=None)
        with mock.patch(self.TARGET) as fake:
            response = self.ask("How?")
        self.assertEqual(response.status_code, 401)
        fake.assert_not_called()

    def test_an_empty_or_too_long_question_gets_400_without_an_ai_call(self):
        with mock.patch(self.TARGET) as fake:
            for question in ["   ", "a" * 1001]:
                response = self.ask(question)
                self.assertEqual(response.status_code, 400)
                self.assertIn("question", response.data)
        fake.assert_not_called()

    def test_an_untrusted_ai_answer_gets_503(self):
        error = AnswerValidationError("Unknown source number: 4")
        with mock.patch(self.TARGET, side_effect=error):
            response = self.ask("How?")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["detail"], UNAVAILABLE)

    def test_an_ai_failure_gets_503_and_hides_the_inner_error(self):
        error = TimeoutError("inner detail")
        with mock.patch(self.TARGET, side_effect=error):
            # The failure is written to the server log, with the details.
            with self.assertLogs("knowledge.views", level="ERROR"):
                response = self.ask("How?")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("inner detail", str(response.data))

    def test_too_many_questions_get_429(self):
        rates = {"knowledge_ask": "2/hour"}
        with (
            mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, rates),
            mock.patch(self.TARGET, return_value=self.FOUND),
        ):
            for _ in range(2):
                self.assertEqual(self.ask("How?").status_code, 200)
            self.assertEqual(self.ask("How?").status_code, 429)


class EvaluateAnswersTests(SimpleTestCase):
    """The evaluation command, with a fake answer_question: no AI, no cost."""

    MODULE = "knowledge.management.commands.evaluate_answers"
    PDR = {"title": "Purchase Delivery Return", "url": "https://docs.example.com/pdr/"}

    def test_check_compares_found_and_pages(self):
        has_answer = {"pages": ["Purchase Delivery Return"]}
        no_answer = {"pages": []}
        order = {"title": "Purchase Order", "url": "https://docs.example.com/po/"}
        self.assertEqual(check(has_answer, Answer(True, "a", [self.PDR])), "pass")
        self.assertEqual(check(has_answer, Answer(False, "n")), "said not found")
        self.assertEqual(
            check(has_answer, Answer(True, "a", [order])), "wrong pages: Purchase Order"
        )
        self.assertEqual(check(no_answer, Answer(False, "n")), "pass")
        self.assertEqual(
            check(no_answer, Answer(True, "a", [order])),
            "answered, but the docs have no answer",
        )

    def test_asks_every_question_and_writes_the_csv(self):
        cases = [
            {
                "question": "PDR কী?",
                "language": "Bangla",
                "pages": ["Purchase Delivery Return"],
            },
            {"question": "help", "language": "English", "pages": []},
        ]

        def fake_answer(question):
            if question == "help":
                raise TimeoutError("too slow")  # one failure must not stop the rest
            return Answer(True, "Return to the supplier.", [self.PDR])

        with tempfile.TemporaryDirectory() as folder:
            questions = Path(folder) / "questions.json"
            questions.write_text(json.dumps(cases), encoding="utf-8")
            output = Path(folder) / "results.csv"
            out = StringIO()
            with (
                mock.patch(f"{self.MODULE}.QUESTIONS_FILE", questions),
                mock.patch(f"{self.MODULE}.PAUSE_SECONDS", 0),
                mock.patch(f"{self.MODULE}.answer_question", side_effect=fake_answer),
            ):
                call_command("evaluate_answers", "--output", str(output), stdout=out)
            with open(output, encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        checks = [row["check"] for row in rows]
        self.assertEqual(checks, ["pass", "error: TimeoutError"])
        self.assertEqual(rows[0]["question"], "PDR কী?")
        self.assertEqual(rows[0]["sources"], self.PDR["url"])
        self.assertEqual(rows[1]["expected_pages"], "(none)")
        self.assertIn("Done: 1 of 2 passed", out.getvalue())

    def test_the_real_question_file_is_well_formed(self):
        cases = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 20)
        for case in cases:
            self.assertEqual(set(case), {"question", "language", "pages"})
            self.assertIn(case["language"], {"English", "Bangla", "Banglish"})


class FakeBusy(Exception):
    """Stands in for openai's InternalServerError (the AI is busy: 5xx)."""


class AnswerRetryTests(TestCase):
    """When the AI is busy for a moment, answer_question tries once more."""

    def setUp(self):
        page = DocPage(
            url="https://docs.example.com/guide/pc/", title="Purchase Cancellation"
        )
        self.results = [
            SearchResult(chunk=DocChunk(page=page, text="Cancel here."), score=0.8)
        ]
        reply = json.dumps({"found": True, "answer": "Cancel here.", "sources": [1]})
        self.good = AIResponse(text=reply, provider="fake", model_name="fake-model")

    def ask(self, *replies):
        """Ask with a fake AI that gives these replies (or errors) in turn."""
        client = mock.Mock()
        client.complete.side_effect = list(replies)
        with (
            mock.patch("knowledge.answer.search_chunks", return_value=self.results),
            mock.patch("knowledge.answer.get_answer_client", return_value=client),
            mock.patch("knowledge.answer.InternalServerError", FakeBusy),
            mock.patch("knowledge.answer.RETRY_WAIT_SECONDS", 0),
        ):
            try:
                return answer_question("How do I cancel?")
            finally:
                self.calls = client.complete.call_count

    def statuses(self):
        logs = AILog.objects.filter(purpose=AILog.Purpose.ANSWER).order_by("id")
        return [log.status for log in logs]

    def test_busy_once_then_the_second_try_answers(self):
        answer = self.ask(FakeBusy("503"), self.good)
        self.assertTrue(answer.found)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.statuses(), [AILog.Status.ERROR, AILog.Status.SUCCESS])

    def test_busy_twice_gives_up(self):
        with self.assertRaises(FakeBusy):
            self.ask(FakeBusy("503"), FakeBusy("503"))
        self.assertEqual(self.calls, 2)  # only one retry

    def test_other_errors_are_not_retried(self):
        with self.assertRaises(TimeoutError):
            self.ask(TimeoutError("too slow"), self.good)
        self.assertEqual(self.calls, 1)

    def test_another_script_once_then_the_second_try_answers(self):
        reply = json.dumps({"found": True, "answer": "সাপ্লায়ரிடம்", "sources": [1]})
        mixed = AIResponse(text=reply, provider="fake", model_name="fake-model")
        answer = self.ask(mixed, self.good)
        self.assertEqual(answer.text, "Cancel here.")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.statuses(), [AILog.Status.INVALID, AILog.Status.SUCCESS])


class ForeignScriptTests(SimpleTestCase):
    """Letters from other scripts (seen in real answers) are caught."""

    def test_the_real_mistakes_are_caught(self):
        self.assertEqual(foreign_letters("সাপ্লায়ரிடம்"), ["ட", "ம", "ர"])  # Tamil
        self.assertEqual(
            foreign_letters("PO এর കീഴിലുള്ള"), ["ക", "ല", "ള", "ഴ"]  # Malayalam
        )

    def test_bangla_english_and_banglish_are_fine(self):
        for text in [
            "Purchase Cancellation-এ যান। ১২৩, ৫%",
            "supplier ke mal ferot dite hobe",
            "café",
        ]:
            self.assertEqual(foreign_letters(text), [])

    def test_an_answer_with_another_script_is_refused(self):
        page = DocPage(url="https://docs.example.com/pdr/", title="PDR")
        results = [SearchResult(chunk=DocChunk(page=page, text="Return."), score=0.8)]
        raw = json.dumps({"found": True, "answer": "সাপ্লায়ரிடம்", "sources": [1]})
        with self.assertRaises(ForeignScriptError):
            parse_answer(raw, results)


FLOWCHART = "flowchart TD\n  A[Purchase Request] --> B[Request for Quotation]"


def overview_reply(**changes):
    """The AI's answer for an overview, as JSON; changes replace parts of it."""
    reply = {
        "summary_en": "Buying goods, from request to payment.",
        "summary_bn": "অনুরোধ থেকে পেমেন্ট পর্যন্ত কেনাকাটা।",
        "flowchart": FLOWCHART,
    }
    reply.update(changes)
    return json.dumps(reply)


class OverviewCheckTests(SimpleTestCase):
    """parse_overview: what is accepted, and what is refused."""

    def test_a_good_answer_is_read_even_inside_a_mermaid_fence(self):
        fenced = overview_reply(flowchart=f"```mermaid\n{FLOWCHART}\n```")
        content = parse_overview("Here it is:\n" + fenced)
        self.assertEqual(content.flowchart, FLOWCHART)
        self.assertEqual(content.summary_bn, "অনুরোধ থেকে পেমেন্ট পর্যন্ত কেনাকাটা।")

    def test_a_click_inside_a_box_label_is_fine(self):
        flowchart = "flowchart TD\n  A[Click Select Winners] --> B[Save]"
        content = parse_overview(overview_reply(flowchart=flowchart))
        self.assertEqual(content.flowchart, flowchart)

    def test_bad_answers_are_refused(self):
        bad_answers = [
            "no json at all",
            overview_reply(flowchart=""),  # missing part
            overview_reply(summary_bn="সাপ্লায়ரிடம்"),  # another script
            overview_reply(flowchart="A --> B"),  # not a Mermaid flowchart
            overview_reply(flowchart=FLOWCHART + '\n  click A href "http://x"'),
            overview_reply(flowchart=FLOWCHART + "\n  C[javascript:alert(1)]"),
            overview_reply(summary_en="word " * 500),  # too long
        ]
        for raw in bad_answers:
            with self.assertRaises(OverviewValidationError):
                parse_overview(raw)


class BuildOverviewsTests(TestCase):
    """build_overviews with a fake AI: no network, no cost."""

    def setUp(self):
        for url, module, title in [
            ("https://docs.example.com/supply-chain/po/", "supply-chain", "PO"),
            ("https://docs.example.com/supply-chain/grn/", "supply-chain", "GRN"),
            ("https://docs.example.com/financial/voucher/", "financial", "Voucher"),
        ]:
            DocPage.objects.create(
                url=url, module=module, title=title, content="Text.", content_hash=url
            )

    def build(self, client, force=False):
        with (
            mock.patch("knowledge.overview.get_overview_client", return_value=client),
            mock.patch("knowledge.overview.RETRY_WAIT_SECONDS", 0),
        ):
            return build_overviews(force=force, report=lambda message: None)

    def test_each_module_gets_one_overview_from_all_its_pages(self):
        client = FakeAIClient(text=overview_reply())
        counts = self.build(client)
        self.assertEqual(counts, {"written": 2, "unchanged": 0, "failed": 0})
        overview = ModuleOverview.objects.get(module="supply-chain")
        self.assertEqual(overview.flowchart, FLOWCHART)
        self.assertEqual(overview.model_name, "fake-model")
        # The AI saw both pages of the module: modules are in name order.
        financial_text, supply_chain_text = client.calls[0][1], client.calls[1][1]
        self.assertIn("## Voucher", financial_text)
        self.assertIn("## GRN", supply_chain_text)
        self.assertIn("## PO", supply_chain_text)

    def test_unchanged_modules_cost_no_ai_call(self):
        self.build(FakeAIClient(text=overview_reply()))
        client = FakeAIClient(text=overview_reply())
        counts = self.build(client)
        self.assertEqual(counts, {"written": 0, "unchanged": 2, "failed": 0})
        self.assertEqual(client.calls, [])

    def test_a_changed_page_rewrites_only_its_module(self):
        self.build(FakeAIClient(text=overview_reply()))
        DocPage.objects.filter(title="GRN").update(content_hash="new")
        counts = self.build(FakeAIClient(text=overview_reply()))
        self.assertEqual(counts, {"written": 1, "unchanged": 1, "failed": 0})

    def test_force_rewrites_all(self):
        self.build(FakeAIClient(text=overview_reply()))
        counts = self.build(FakeAIClient(text=overview_reply()), force=True)
        self.assertEqual(counts["written"], 2)

    def test_an_unusable_answer_is_tried_once_more(self):
        client = mock.Mock()
        good = AIResponse(text=overview_reply(), provider="f", model_name="m")
        bad = AIResponse(text="no json", provider="f", model_name="m")
        client.complete.side_effect = [bad, good, good]  # financial: bad, then good
        counts = self.build(client)
        self.assertEqual(counts, {"written": 2, "unchanged": 0, "failed": 0})
        self.assertEqual(client.complete.call_count, 3)

    def test_a_module_that_fails_twice_is_skipped_and_nothing_is_saved(self):
        counts = self.build(FakeAIClient(text="no json"))
        self.assertEqual(counts, {"written": 0, "unchanged": 0, "failed": 2})
        self.assertFalse(ModuleOverview.objects.exists())
        invalid = AILog.objects.filter(
            purpose=AILog.Purpose.OVERVIEW, status=AILog.Status.INVALID
        )
        self.assertEqual(invalid.count(), 4)  # 2 modules x 2 tries

    def test_without_ai_settings_it_refuses_to_run(self):
        with self.assertRaises(RuntimeError):
            self.build(None)


class BuildOverviewsCommandTests(SimpleTestCase):
    TARGET = "knowledge.management.commands.build_overviews.build_overviews"

    def test_passes_force_and_prints_the_totals(self):
        totals = {"written": 1, "unchanged": 0, "failed": 0}
        out = StringIO()
        with mock.patch(self.TARGET, return_value=totals) as fake:
            call_command("build_overviews", "--force", stdout=out)
        self.assertTrue(fake.call_args.kwargs["force"])
        self.assertIn("Done: written 1, unchanged 0, failed 0", out.getvalue())


MODULES_URL = "/api/v1/knowledge/modules/"


class ModuleOverviewApiTests(APITestCase):
    """GET the module overviews: read from the database, no AI call."""

    def setUp(self):
        rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.client.force_authenticate(user=rahim)
        ModuleOverview.objects.create(
            module="supply-chain",
            summary_en="Buying goods.",
            summary_bn="কেনাকাটা।",
            flowchart=FLOWCHART,
            source_hash="abc",
            model_name="fake-model",
        )

    def test_the_list_shows_every_module(self):
        response = self.client.get(MODULES_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["module"], "supply-chain")

    def test_one_module_has_its_summaries_and_flowchart(self):
        response = self.client.get(MODULES_URL + "supply-chain/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary_en"], "Buying goods.")
        self.assertEqual(response.data["summary_bn"], "কেনাকাটা।")
        self.assertEqual(response.data["flowchart"], FLOWCHART)
        self.assertNotIn("source_hash", response.data)  # stays inside

    def test_an_unknown_module_is_404(self):
        response = self.client.get(MODULES_URL + "no-such-module/")
        self.assertEqual(response.status_code, 404)

    def test_login_is_required(self):
        self.client.force_authenticate(user=None)
        for url in [MODULES_URL, MODULES_URL + "supply-chain/"]:
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_reading_an_overview_makes_no_ai_call(self):
        self.client.get(MODULES_URL + "supply-chain/")
        self.assertFalse(AILog.objects.exists())
