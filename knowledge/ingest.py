"""Turning documentation web pages into text for the knowledge base.

Phase 5, Step 2. extract_page reads one page's HTML and keeps only the page's
own content: the title, the text split by headings, and the image addresses.
Menus, footers and other site parts are dropped, and tables become lines of
text, because most of the documentation's facts are in tables.
"""

from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Parts of a Docusaurus page that belong to the site, not to the page itself.
SITE_PARTS = (
    ".theme-doc-breadcrumbs, .theme-doc-footer, .theme-doc-toc-mobile, "
    ".pagination-nav, .hash-link"
)
HEADINGS = ("h2", "h3", "h4")
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
