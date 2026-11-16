"""Tests for the knowledge base models, including a real vector search."""

from django.db import IntegrityError
from django.test import TestCase, SimpleTestCase
from pgvector.django import CosineDistance

from .models import DocChunk, DocPage
from .ingest import (
    Section,
    extract_page,
    module_from_url,
    save_page,
    split_into_chunks,
)


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
