"""manage.py fetch_docs: bring documentation pages into the knowledge base.

Example:
    python manage.py fetch_docs https://docs.prismerp.net/supply-chain/ --limit 20
"""

from django.core.management.base import BaseCommand

from knowledge.ingest import crawl


class Command(BaseCommand):
    help = "Fetch documentation pages, starting at a URL, and save them as chunks."

    def add_arguments(self, parser):
        parser.add_argument(
            "start_url", help="First page; only pages below it are fetched."
        )
        parser.add_argument(
            "--limit", type=int, default=50, help="Most pages to fetch (default 50)."
        )

    def handle(self, *args, **options):
        counts = crawl(
            options["start_url"], limit=options["limit"], report=self.stdout.write
        )
        summary = ", ".join(f"{name} {number}" for name, number in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
