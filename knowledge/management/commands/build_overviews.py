"""manage.py build_overviews: write module overviews (summary and flowchart).

Only modules whose pages changed since the last overview are written again,
so it is safe to run after every fetch_docs. Example:
    python manage.py build_overviews
    python manage.py build_overviews --force   # all again, e.g. new instructions
"""

from django.core.management.base import BaseCommand, CommandError

from knowledge.overview import build_overviews


class Command(BaseCommand):
    help = "Write the overview of each module whose pages changed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Write every overview again, even if its pages did not change.",
        )

    def handle(self, *args, **options):
        try:
            counts = build_overviews(force=options["force"], report=self.stdout.write)
        except RuntimeError as error:  # AI not set up in .env
            raise CommandError(error)
        summary = ", ".join(f"{name} {number}" for name, number in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
