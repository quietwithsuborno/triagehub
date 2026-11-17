"""Tests for the knowledge base models, including a real vector search."""

import requests

from io import StringIO
from unittest import mock
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.test import TestCase, SimpleTestCase
from pgvector.django import CosineDistance
from .images import describe_new_images, fetch_image
from .models import DocChunk, DocPage, ImageDescription
from .ingest import (
    Section,
    crawl,
    extract_page,
    module_from_url,
    save_page,
    split_into_chunks,
)
from tickets.models import AILog
from tickets.triage import FakeAIClient


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
        chunk = self.make_chunk(0, embedding=[0.5, 0.25, 1.0])
        chunk.refresh_from_db()
        self.assertEqual(list(chunk.embedding), [0.5, 0.25, 1.0])

    # A first taste of Step 4: the database sorts chunks by closeness in meaning.
    def test_nearest_chunk_comes_first(self):
        self.make_chunk(0, embedding=[0.0, 1.0])  # points another way: far
        self.make_chunk(1, embedding=[1.0, 0.0])  # same direction as the question
        self.make_chunk(2, embedding=[0.9, 0.1])  # almost the same direction
        question = [1.0, 0.0]
        nearest = DocChunk.objects.order_by(CosineDistance("embedding", question))
        self.assertEqual([c.position for c in nearest], [1, 2, 0])


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
        chunk.embedding = [1.0, 0.0]
        chunk.save()

        self.assertEqual(self.save(), "unchanged")
        chunk.refresh_from_db()  # still there, with its embedding
        self.assertEqual(list(chunk.embedding), [1.0, 0.0])

    def test_changed_page_gets_new_chunks(self):
        self.save()
        DocChunk.objects.update(embedding=[1.0, 0.0])
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
        with mock.patch(self.TARGET, return_value=totals) as fake:
            call_command("describe_images", "--limit", "2", stdout=out)
        self.assertEqual(fake.call_args.kwargs["limit"], 2)
        self.assertIn("Done: described 2, failed 0, left 5", out.getvalue())

    def test_missing_ai_settings_give_a_clear_error(self):
        with mock.patch(self.TARGET, side_effect=RuntimeError("AI is not set up")):
            with self.assertRaises(CommandError):
                call_command("describe_images")
