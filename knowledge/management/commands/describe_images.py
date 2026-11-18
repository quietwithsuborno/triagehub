"""manage.py describe_images: have the AI describe documentation images.

Only images without a description are sent, so it is safe to run again.
Example:
    python manage.py describe_images --limit 5
"""

from django.core.management.base import BaseCommand, CommandError
from knowledge.ingest import add_all_image_chunks
from knowledge.images import describe_new_images


class Command(BaseCommand):
    help = "Describe documentation images that have no description yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Most images in this run (default 20).",
        )

    def handle(self, *args, **options):
        try:
            counts = describe_new_images(
                limit=options["limit"], report=self.stdout.write
            )
        except RuntimeError as error:  # AI not set up in .env
            raise CommandError(error)

        counts["image chunks"] = add_all_image_chunks()
        summary = ", ".join(f"{name} {number}" for name, number in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
