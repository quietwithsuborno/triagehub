"""Database models for the documentation knowledge base.

A DocPage is one page of the product documentation, fetched from the website.
Each page is split into DocChunks: short parts, one topic each. Every chunk
gets an embedding (a list of numbers that describes its meaning), so that a
question can find the chunks closest to it in meaning.
"""

from django.db import models
from pgvector.django import VectorField

# How many numbers each embedding has. The model can give 128 to 3072;
# 768 is 4 times smaller than the full 3072 and loses little. Changing it
# needs a migration and new embeddings for every chunk.
EMBEDDING_DIMENSIONS = 768


class DocPage(models.Model):
    """One documentation page, as fetched from the website."""

    # The page's address. Also the link shown with every answer.
    url = models.URLField(max_length=500, unique=True)
    # First part of the URL path, e.g. "supply-chain". Groups pages into
    # modules for the module overview.
    module = models.CharField(max_length=50, db_index=True)
    title = models.CharField(max_length=300)
    content = models.TextField()  # the page's text, without menus and HTML
    # A fingerprint of the content. If a page has not changed since the last
    # fetch, the fingerprint is the same and its chunks are not rebuilt.
    content_hash = models.CharField(max_length=64)
    summary = models.TextField(blank=True)  # short AI summary, for module overviews
    # Addresses of the images on the page, kept for describing them later.
    image_urls = models.JSONField(default=list, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)  # last time it was saved

    class Meta:
        ordering = ["url"]

    def __str__(self):
        return self.title


class DocChunk(models.Model):
    """A short part of one page, about one topic, with its embedding."""

    # CASCADE: a chunk is a piece of its page and means nothing without it.
    page = models.ForeignKey(DocPage, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()  # 0, 1, 2... order within the page
    heading = models.CharField(max_length=300, blank=True)  # its section heading
    text = models.TextField()

    # The meaning as numbers, made by the embedding model (Step 3).
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)

    # Which model made the embedding. Vectors from different models cannot be
    # compared, so changing the model means making every embedding again.
    embedding_model = models.CharField(max_length=100, blank=True)
    # Set when the chunk was made from an image's description, empty for text.
    image_url = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["page", "position"]
        constraints = [
            # One page cannot have two chunks at the same position.
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_chunk_position"
            ),
        ]

    def __str__(self):
        return f"{self.page.title} #{self.position}"


class ImageDescription(models.Model):
    """What the AI read in one documentation image (a screenshot or diagram).

    Kept per image address, not per page: the same image can be on several
    pages, and it is described only once.
    """

    url = models.URLField(max_length=500, unique=True)
    description = models.TextField()
    model_name = models.CharField(max_length=100)  # which AI model wrote it
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["url"]

    def __str__(self):
        return self.url


class ModuleOverview(models.Model):
    """An AI-written overview of one documentation module, made in advance.

    Made once from the module's pages and kept here, so showing it costs no
    AI call. It is made again only when the module's pages change.
    """

    module = models.CharField(max_length=50, unique=True)
    summary_en = models.TextField()
    summary_bn = models.TextField()
    flowchart = models.TextField()  # Mermaid text; the browser draws it
    source_hash = models.CharField(max_length=64)  # fingerprint of the pages used
    model_name = models.CharField(max_length=100)  # which AI model wrote it
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module"]

    def __str__(self):
        return self.module
