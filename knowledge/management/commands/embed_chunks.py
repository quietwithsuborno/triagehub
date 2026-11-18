"""manage.py embed_chunks: make embeddings for chunks that need one.

Only chunks without an embedding (or with one from another model) are sent,
so it is safe to run again.
Example:
    python manage.py embed_chunks --limit 40
"""

from django.core.management.base import BaseCommand, CommandError

from knowledge.embeddings import embed_new_chunks


class Command(BaseCommand):
    help = "Make embeddings for documentation chunks that need one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Most chunks in this run (default 200).",
        )

    def handle(self, *args, **options):
        try:
            counts = embed_new_chunks(limit=options["limit"], report=self.stdout.write)
        except RuntimeError as error:  # AI not set up in .env
            raise CommandError(error)
        summary = ", ".join(f"{name} {number}" for name, number in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
