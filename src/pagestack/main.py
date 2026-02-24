#!/usr/bin/env python3
"""
PageStack — Convert a list of URLs into a single EPUB file.

Usage:
    pagestack urls.txt
    pagestack urls.txt my_reading_list.epub
    pagestack urls.txt --title "My Articles" --author "Arpit"

The input file should have one URL per line. Lines starting with # are ignored.
"""

import sys
import os
import hashlib
import mimetypes
import argparse
import uuid
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from datetime import datetime
import io

import requests
from bs4 import BeautifulSoup, Comment
from readability import Document
from ebooklib import epub
import ebooklib.utils as _epub_utils

# Workaround for ebooklib bug: get_pages() crashes when get_body_content()
# returns b"" (e.g. for EpubNav before its content is written).
_orig_get_pages = _epub_utils.get_pages

def _safe_get_pages(item):
    body = item.get_body_content()
    if not body:
        return []
    return _orig_get_pages(item)

_epub_utils.get_pages = _safe_get_pages

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch_page(url: str, session: requests.Session, timeout: int = 30) -> tuple[str, str]:
    """Fetch a page. Returns (html_text, final_url_after_redirects)."""
    resp = session.get(url, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    # Honour charset from headers; fallback to apparent encoding
    resp.encoding = resp.apparent_encoding
    return resp.text, str(resp.url)


def fetch_binary(url: str, session: requests.Session, timeout: int = 20) -> tuple[bytes, str] | None:
    """Fetch a binary resource (image). Returns (data, mime_type) or None."""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        if not ctype:
            ext = Path(urlparse(url).path).suffix.lower()
            ctype = mimetypes.types_map.get(ext, "image/jpeg")
        return resp.content, ctype
    except Exception as exc:
        print(f"    [warn] image download failed ({url}): {exc}")
        return None


# ---------------------------------------------------------------------------
# Content extraction & cleaning
# ---------------------------------------------------------------------------

def extract_article(html: str, url: str) -> tuple[str, str]:
    """
    Use readability to pull out the main article body.
    Returns (title, body_html).
    """
    doc = Document(html)
    title = doc.short_title() or doc.title() or urlparse(url).netloc
    body = doc.summary(html_partial=True)
    return title.strip(), body


_UNWANTED_TAGS = {"script", "style", "noscript", "iframe", "object",
                   "embed", "form", "button", "input", "select", "textarea",
                   "nav", "aside", "footer", "header", "advertisement"}

_UNWANTED_ATTRS = {
    "onclick", "onload", "onerror", "class", "id", "style",
    "data-src-retina", "data-original", "loading", "decoding",
    "fetchpriority", "sizes", "crossorigin", "referrerpolicy",
}


def _sanitise_attrs(tag):
    """Remove most HTML attributes, keeping only safe presentational ones."""
    keep = {"src", "href", "alt", "title", "colspan", "rowspan", "width", "height"}
    for attr in list(tag.attrs.keys()):
        if attr not in keep:
            del tag.attrs[attr]


def process_images(
    soup: BeautifulSoup,
    base_url: str,
    book: epub.EpubBook,
    session: requests.Session,
    image_cache: dict,
) -> None:
    """
    For each <img> in soup:
      1. Resolve the URL.
      2. Download (or reuse cached).
      3. Add to the EPUB as a media item.
      4. Rewrite src to the EPUB-internal path.
    """
    for img in soup.find_all("img"):
        # Prefer data-src / data-lazy-src over src for lazy-loaded images
        src = (
            img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("data-original")
            or img.get("src", "")
        ).strip()

        if not src:
            img.decompose()
            continue

        # Skip tiny tracking pixels / spacers
        w = img.get("width", "")
        h = img.get("height", "")
        try:
            if int(w) <= 2 or int(h) <= 2:
                img.decompose()
                continue
        except (ValueError, TypeError):
            pass

        # Skip data URIs — leave them as-is (already embedded)
        if src.startswith("data:"):
            continue

        abs_url = urljoin(base_url, src)

        if abs_url in image_cache:
            img["src"] = image_cache[abs_url]
            _sanitise_attrs(img)
            continue

        result = fetch_binary(abs_url, session)
        if result is None:
            img.decompose()
            continue

        data, mime_type = result

        # Optionally resize very large images so Kindle doesn't choke
        if HAS_PIL and mime_type in ("image/jpeg", "image/png", "image/gif"):
            try:
                pil_img = Image.open(io.BytesIO(data))
                max_dim = 1600
                if pil_img.width > max_dim or pil_img.height > max_dim:
                    pil_img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                    buf = io.BytesIO()
                    fmt = "JPEG" if mime_type == "image/jpeg" else pil_img.format or "PNG"
                    pil_img.save(buf, format=fmt)
                    data = buf.getvalue()
            except Exception:
                pass  # use original data if PIL fails

        url_hash = hashlib.md5(abs_url.encode()).hexdigest()[:10]
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        if ext in (".jpe", ".jpeg"):
            ext = ".jpg"
        epub_path = f"images/{url_hash}{ext}"

        epub_img = epub.EpubItem(
            uid=f"img_{url_hash}",
            file_name=epub_path,
            media_type=mime_type,
            content=data,
        )
        book.add_item(epub_img)
        image_cache[abs_url] = epub_path

        img["src"] = epub_path
        _sanitise_attrs(img)

        # Wrap bare <img> in a <figure> for better Kindle rendering
        if img.parent and img.parent.name not in ("figure", "p", "a"):
            figure = soup.new_tag("figure")
            img.replace_with(figure)
            figure.append(img)
            alt = img.get("alt", "").strip()
            if alt:
                cap = soup.new_tag("figcaption")
                cap.string = alt
                figure.append(cap)


def clean_html(
    raw_html: str,
    base_url: str,
    book: epub.EpubBook,
    session: requests.Session,
    image_cache: dict,
) -> str:
    """
    Full pipeline: parse → remove junk → process images → clean attrs → serialise.
    """
    soup = BeautifulSoup(raw_html, "lxml")

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Drop unwanted tags entirely
    for tag in soup.find_all(_UNWANTED_TAGS):
        tag.decompose()

    # Handle srcset: pick the best src before we strip attrs
    for img in soup.find_all("img"):
        srcset = img.get("srcset", "")
        if srcset and not img.get("src"):
            # Take the last (usually highest-res) candidate
            candidates = [s.strip().split()[0] for s in srcset.split(",") if s.strip()]
            if candidates:
                img["src"] = candidates[-1]
        if "srcset" in img.attrs:
            del img.attrs["srcset"]

    # Process and embed images
    process_images(soup, base_url, book, session, image_cache)

    # Fix relative links → absolute so they work in the EPUB
    for a in soup.find_all("a", href=True):
        a["href"] = urljoin(base_url, a["href"])

    # Strip unwanted attributes from all remaining tags
    for tag in soup.find_all(True):
        if tag.name == "img":
            continue  # already handled
        for attr in list(tag.attrs.keys()):
            if attr in _UNWANTED_ATTRS:
                del tag.attrs[attr]

    # Remove empty block elements (but keep <img> wrappers)
    for tag in soup.find_all(["p", "div", "span"]):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()

    # Return inner body content (not the <body> tag itself) to avoid nested
    # <body> tags when this is embedded in our chapter XHTML template.
    body = soup.find("body")
    return body.decode_contents() if body else str(soup)


# ---------------------------------------------------------------------------
# EPUB construction helpers
# ---------------------------------------------------------------------------

CSS = """\
/* ---- Reset ---- */
* { margin: 0; padding: 0; box-sizing: border-box; }

/* ---- Body ---- */
body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1em;
    line-height: 1.7;
    color: #1a1a1a;
    padding: 1em 1.2em;
    max-width: 40em;
    margin: 0 auto;
}

/* ---- Headings ---- */
h1, h2, h3, h4, h5, h6 {
    font-family: "Helvetica Neue", Arial, sans-serif;
    line-height: 1.25;
    color: #111;
    margin-top: 1.4em;
    margin-bottom: 0.4em;
    page-break-after: avoid;
}
h1 { font-size: 1.7em; }
h2 { font-size: 1.4em; }
h3 { font-size: 1.2em; }

/* Chapter title generated by this tool */
h1.chapter-title {
    font-size: 1.9em;
    border-bottom: 2px solid #333;
    padding-bottom: 0.3em;
    margin-top: 0;
}
p.source-url {
    font-size: 0.78em;
    color: #888;
    word-break: break-all;
    margin-bottom: 1em;
}
hr.chapter-rule { border: none; border-top: 1px solid #ccc; margin: 0.8em 0 1.2em; }

/* ---- Paragraphs ---- */
p { margin: 0.7em 0; text-align: justify; }

/* ---- Code ---- */
pre {
    font-family: "Courier New", Courier, "Lucida Console", monospace;
    font-size: 0.82em;
    background: #f6f6f6;
    border: 1px solid #d4d4d4;
    border-left: 4px solid #555;
    border-radius: 3px;
    padding: 0.8em 1em;
    margin: 1em 0;
    white-space: pre-wrap;
    word-wrap: break-word;
    overflow-wrap: break-word;
    line-height: 1.45;
    page-break-inside: avoid;
}
code {
    font-family: "Courier New", Courier, "Lucida Console", monospace;
    font-size: 0.88em;
    background: #f0f0f0;
    border: 1px solid #ddd;
    border-radius: 2px;
    padding: 0.1em 0.35em;
}
pre code {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 1em;
}

/* ---- Images ---- */
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em auto;
}
figure {
    margin: 1.2em 0;
    text-align: center;
}
figcaption {
    font-size: 0.82em;
    color: #666;
    margin-top: 0.3em;
    font-style: italic;
}

/* ---- Blockquote ---- */
blockquote {
    border-left: 4px solid #bbb;
    margin: 1em 0;
    padding: 0.4em 1em;
    color: #444;
    background: #fafafa;
}

/* ---- Tables ---- */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
    font-size: 0.88em;
}
th, td {
    border: 1px solid #ccc;
    padding: 0.45em 0.7em;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f0f0f0;
    font-weight: bold;
}

/* ---- Lists ---- */
ul, ol { margin: 0.7em 0; padding-left: 2em; }
li { margin: 0.25em 0; }

/* ---- Links ---- */
a { color: #1a5aa0; word-break: break-all; }

/* ---- Misc ---- */
hr { border: none; border-top: 1px solid #ddd; margin: 1em 0; }
abbr { text-decoration: underline dotted; }
mark { background: #fff3b0; }
sup, sub { font-size: 0.75em; line-height: 0; }
"""


def make_chapter_body(title: str, url: str, content_html: str) -> str:
    """
    Return the *body-level* HTML for a chapter (no <html>/<head>/<body> wrapper).
    ebooklib's get_content() wraps this in the proper XHTML template itself, adding
    the <head> with title/links from chap.title / chap.add_link().
    Passing a full document with an XML declaration causes a silent ValueError in
    ebooklib's parse_html_string(), which returns b"" instead of the content.
    """
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    safe_url = url.replace("&", "&amp;").replace('"', "&quot;")
    return (
        f'<h1 class="chapter-title">{safe_title}</h1>\n'
        f'<p class="source-url">Source: <a href="{safe_url}">{safe_url}</a></p>\n'
        f'<hr class="chapter-rule"/>\n'
        f'{content_html}'
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def read_urls(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    urls = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def build_epub(
    urls: list[str],
    output_path: str,
    title: str,
    author: str,
    timeout: int,
) -> int:
    """Process URLs and write the EPUB. Returns number of successful chapters."""
    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    # Stylesheet
    css_item = epub.EpubItem(
        uid="styles",
        file_name="styles.css",
        media_type="text/css",
        content=CSS.encode("utf-8"),
    )
    book.add_item(css_item)

    session = make_session()
    chapters: list[epub.EpubHtml] = []
    image_cache: dict[str, str] = {}

    for idx, url in enumerate(urls, start=1):
        print(f"\n[{idx}/{len(urls)}] {url}")
        try:
            html, final_url = fetch_page(url, session, timeout)
        except Exception as exc:
            print(f"  [error] fetch failed: {exc}")
            continue

        try:
            article_title, raw_content = extract_article(html, final_url)
        except Exception as exc:
            print(f"  [error] extraction failed: {exc}")
            continue

        print(f"  Title : {article_title}")

        try:
            clean = clean_html(raw_content, final_url, book, session, image_cache)
        except Exception as exc:
            print(f"  [error] cleaning failed: {exc}")
            clean = raw_content  # fall back to unprocessed content

        # Store only body-level HTML — ebooklib builds the <html>/<head> wrapper.
        # Passing a full document with <?xml ...?> declaration causes a silent
        # ValueError inside ebooklib's parse_html_string(), yielding 0-byte chapters.
        body_html = make_chapter_body(article_title, final_url, clean)

        chap = epub.EpubHtml(
            title=article_title,
            file_name=f"chapter_{idx:03d}.xhtml",
            lang="en",
        )
        chap.content = body_html
        chap.add_link(href="styles.css", rel="stylesheet", type="text/css")
        book.add_item(chap)
        chapters.append(chap)
        print(f"  OK    ({len(clean):,} chars, {sum(1 for k, v in image_cache.items() if v.startswith('images/'))} images total so far)")

    if not chapters:
        print("\n[error] No chapters were successfully created.")
        return 0

    # Table of contents (NCX + Navigation Document)
    book.toc = [epub.Link(c.file_name, c.title, c.file_name) for c in chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Spine: nav page first, then chapters in order
    book.spine = ["nav"] + chapters

    epub.write_epub(output_path, book, {})
    return len(chapters)


def main():
    parser = argparse.ArgumentParser(
        description="PageStack: Convert a list of URLs to a single EPUB file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("urls_file", help="Path to text file with one URL per line")
    parser.add_argument(
        "output",
        nargs="?",
        default="",
        help="Output EPUB filename (default: derived from title + date)",
    )
    parser.add_argument("--title", default="", help="Book title (default: auto-generated)")
    parser.add_argument("--author", default="PageStack", help="Author field in EPUB metadata")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP request timeout in seconds")
    args = parser.parse_args()

    urls_file = Path(args.urls_file)
    if not urls_file.exists():
        sys.exit(f"Error: '{urls_file}' not found.")

    urls = read_urls(urls_file)
    if not urls:
        sys.exit("Error: no URLs found in file (use one URL per line; # for comments).")

    print(f"Found {len(urls)} URL(s) in {urls_file}")

    book_title = args.title or f"Web Articles — {datetime.now().strftime('%Y-%m-%d')}"
    output_path = args.output or re.sub(r"[^\w\-.]", "_", book_title) + ".epub"

    print(f"Output : {output_path}")
    print(f"Title  : {book_title}")

    n = build_epub(
        urls=urls,
        output_path=output_path,
        title=book_title,
        author=args.author,
        timeout=args.timeout,
    )

    if n:
        size_kb = Path(output_path).stat().st_size // 1024
        print(f"\nDone! EPUB written to: {output_path}")
        print(f"  {n}/{len(urls)} articles  |  {size_kb} KB")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
