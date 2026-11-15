"""Tests for the knowledge base models, including a real vector search."""

from django.db import IntegrityError
from django.test import TestCase
from pgvector.django import CosineDistance

from .models import DocChunk, DocPage


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
