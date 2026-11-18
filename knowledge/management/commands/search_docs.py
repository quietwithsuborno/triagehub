"""manage.py search_docs: find the documentation chunks closest to a question.

No answer is written, only what was found and how close it is, so the
search can be tried by hand. Example:
    python manage.py search_docs "How do I cancel a purchase order?"
"""

from django.core.management.base import BaseCommand, CommandError

from knowledge.search import search_chunks

PREVIEW_CHARS = 120  # how much of each chunk's text to show


class Command(BaseCommand):
    help = "Show the documentation chunks closest in meaning to a question."

    def add_arguments(self, parser):
        parser.add_argument("question")
        parser.add_argument(
            "--limit", type=int, default=5, help="How many chunks to show (default 5)."
        )

    def handle(self, *args, **options):
        try:
            results = search_chunks(options["question"], limit=options["limit"])
        except (ValueError, RuntimeError) as error:  # bad question, or no AI
            raise CommandError(error)
        if not results:
            self.stdout.write("No embedded chunks to search. Run embed_chunks first.")
            return
        for rank, result in enumerate(results, start=1):
            chunk = result.chunk
            heading = f" > {chunk.heading}" if chunk.heading else ""
            # The text on one line: line breaks become spaces.
            preview = " ".join(chunk.text.split())[:PREVIEW_CHARS]
            title = f"{chunk.page.title}{heading}"
            self.stdout.write(f"{rank}. {result.score:.3f}  {title}")
            self.stdout.write(f"   {chunk.page.url}")
            self.stdout.write(f"   {preview}")
