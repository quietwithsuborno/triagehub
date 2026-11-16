"""Turning documentation web pages into text for the knowledge base.

Phase 5, Step 2. extract_page reads one page's HTML and keeps only the page's
own content: the title, the text split by headings, and the image addresses.
Menus, footers and other site parts are dropped, and tables become lines of
text, because most of the documentation's facts are in tables. save_page then
cuts the text into chunks and stores the page and its chunks in the database.
"""

import hashlib
import time
import requests

from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from django.db import transaction
from .models import DocChunk, DocPage

# Parts of a Docusaurus page that belong to the site, not to the page itself.
SITE_PARTS = (
    ".theme-doc-breadcrumbs, .theme-doc-footer, .theme-doc-toc-mobile, "
    ".pagination-nav, .hash-link"
)
HEADINGS = ("h2", "h3", "h4")
# About one or two paragraphs: big enough to keep its meaning, small enough
# that a search finds the exact part that answers a question.
MAX_CHUNK_WORDS = 300
BLOCKS = (*HEADINGS, "p", "li", "table", "pre")


@dataclass
class Section:
    """The text under one heading (heading is "" for the part before the first)."""

    heading: str
    text: str


@dataclass
class PageContent:
    """What extract_page found on one page."""

    title: str
    sections: list[Section] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)


def clean(element):
    """The element's visible text on one line, with single spaces."""
    return " ".join(element.get_text(" ").split())


def table_to_lines(table):
    """Turn a table into lines of text, one per row.

    A two-column row becomes "first: second" (most tables are "Field" and
    "Description"). Wider rows become "Header: value; Header: value".
    """
    rows = []
    for row in table.find_all("tr"):
        cells = [clean(cell) for cell in row.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []

    first_row = table.find("tr")
    has_header = all(cell.name == "th" for cell in first_row.find_all(["th", "td"]))
    header = rows[0] if has_header else None
    lines = []
    for cells in rows[1:] if has_header else rows:
        if len(cells) == 2:
            lines.append(f"{cells[0]}: {cells[1]}")
        elif header and len(header) == len(cells):
            pairs = [f"{name}: {value}" for name, value in zip(header, cells) if value]
            lines.append("; ".join(pairs))
        else:
            lines.append(" | ".join(cells))
    return lines


def extract_page(html, url):
    """Return the title, the sections and the image addresses of one page."""
    soup = BeautifulSoup(html, "html.parser")
    # Docusaurus puts the page's own content in <article>.
    main = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in main.find_all(["nav", "script", "style", "button", "footer"]):
        tag.decompose()
    for tag in main.select(SITE_PARTS):
        tag.decompose()

    h1 = main.find("h1")
    if h1:
        title = clean(h1)
    elif soup.title:
        title = clean(soup.title)
    else:
        title = url
    page = PageContent(title=title)

    for image in main.find_all("img"):
        if image.get("src"):
            address = urljoin(url, image["src"])  # make relative addresses full
            if address not in page.image_urls:
                page.image_urls.append(address)

    heading, lines = "", []
    for element in main.find_all(BLOCKS):
        # Text inside a list item, table or code block is already taken
        # together with that bigger block, so skip it here.
        if element.find_parent(["li", "table", "pre"]):
            continue
        if element.name in HEADINGS:
            if lines:
                page.sections.append(Section(heading, "\n".join(lines)))
            heading, lines = clean(element), []
        elif element.name == "table":
            lines.extend(table_to_lines(element))
        elif element.name == "li":
            lines.append(f"- {clean(element)}")
        elif text := clean(element):
            lines.append(text)
    if lines:
        page.sections.append(Section(heading, "\n".join(lines)))
    return page


def module_from_url(url):
    """The first part of the address path: .../supply-chain/x/ -> "supply-chain"."""
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[0] if parts else ""


def page_text(page):
    """The whole page as one text, headings included (for DocPage.content)."""
    parts = []
    for section in page.sections:
        parts.append(
            f"{section.heading}\n{section.text}" if section.heading else section.text
        )
    return "\n\n".join(parts)


def fingerprint(page):
    """A SHA-256 fingerprint of the page's text and images.

    The same content always gives the same 64 characters; any change, even
    one letter, gives a completely different one.
    """
    data = page_text(page) + "\n" + "\n".join(page.image_urls)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def split_into_chunks(sections, max_words=MAX_CHUNK_WORDS):
    """Return (heading, text) pairs: one per section, long ones cut in parts.

    A long section is cut between lines, never inside one, so a table row
    stays whole. A single line longer than max_words stays as one chunk.
    """
    chunks = []
    for section in sections:
        lines, words = [], 0
        for line in section.text.split("\n"):
            line_words = len(line.split())
            if lines and words + line_words > max_words:
                chunks.append((section.heading, "\n".join(lines)))
                lines, words = [], 0
            lines.append(line)
            words += line_words
        if lines:
            chunks.append((section.heading, "\n".join(lines)))
    return chunks


@transaction.atomic
def save_page(url, page):
    """Store one extracted page and its chunks in the database.

    Returns "created", "updated" or "unchanged". An unchanged page (same
    fingerprint as last time) is left alone, so its chunks keep their
    embeddings and no free AI quota is spent on it again.
    """
    content_hash = fingerprint(page)
    existing = DocPage.objects.filter(url=url).first()
    if existing and existing.content_hash == content_hash:
        return "unchanged"

    doc, created = DocPage.objects.update_or_create(
        url=url,
        defaults={
            "module": module_from_url(url),
            "title": page.title,
            "content": page_text(page),
            "content_hash": content_hash,
            "image_urls": page.image_urls,
            "summary": "",  # the old summary no longer matches the new text
        },
    )
    # The old chunks (and their embeddings) no longer match the new text.
    doc.chunks.all().delete()
    DocChunk.objects.bulk_create(
        DocChunk(page=doc, position=position, heading=heading, text=text)
        for position, (heading, text) in enumerate(split_into_chunks(page.sections))
    )
    return "created" if created else "updated"


# --- Fetching pages from the website (Step 2, part D) ---

# Who we are, sent with every request, so the site owner can see it is us.
USER_AGENT = "TriageHubDocsBot/1.0 (internship project, knowledge assistant)"
REQUEST_TIMEOUT = 15  # seconds to wait for one page before giving up
PAUSE_SECONDS = 1.0  # wait between pages, so we never put load on their server


def fetch_html(url):
    """Download one page and return its HTML. Raises an error on 404, 500..."""
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.text


def find_links(html, page_url, start_url):
    """Addresses linked from this page that are inside the start_url section."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        # Full address, without "#part" or "?query" (same page, other spot).
        address = urljoin(page_url, anchor["href"]).split("#")[0].split("?")[0]
        if address.startswith(start_url) and address not in links:
            links.append(address)
    return links


def crawl(start_url, limit=50, pause=PAUSE_SECONDS, report=print):
    """Fetch and save pages, starting at start_url and following its links.

    Only pages below start_url are visited, at most `limit` of them, with a
    pause between pages, and never a page that robots.txt forbids. Returns
    how many pages were created, updated, unchanged, skipped or failed.
    """
    if not start_url.endswith("/"):
        start_url += "/"  # so ".../lifecycle" does not also match ".../lifecycle-2"
    robots = robotparser.RobotFileParser(urljoin(start_url, "/robots.txt"))
    robots.read()

    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    queue, seen, visited = [start_url], {start_url}, 0
    while queue and visited < limit:
        url = queue.pop(0)
        if not robots.can_fetch(USER_AGENT, url):
            counts["skipped"] += 1
            report(f"skipped (robots.txt): {url}")
            continue

        visited += 1
        try:
            html = fetch_html(url)
        except requests.RequestException as error:
            counts["failed"] += 1
            report(f"failed: {url} ({error})")
        else:
            result = save_page(url, extract_page(html, url))
            counts[result] += 1
            report(f"{result}: {url}")
            for link in find_links(html, url, start_url):
                if link not in seen:
                    seen.add(link)
                    queue.append(link)
        time.sleep(pause)
    return counts
