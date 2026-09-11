import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
import re
import time


# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://en.wikipedia.org/"
ALLOWED_DOMAIN = "en.wikipedia.org"
URL_LIST_FILE = "wikipedia_top_1m_urls.txt"
OUTPUT_DIR = Path("WikiMillion")

TIMEOUT = 20
MAX_RETRIES = 5
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalArchiveBot/1.0)"
}


# ============================================================
# Extensions that should not be crawled
# ============================================================

SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",

    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",

    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".mp4",
    ".webm",
    ".avi",
    ".mov",

    ".css",
    ".js",
    ".json",
    ".xml",
    ".rss",

    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
}

def load_url_list(filename):
    """
    Load URLs from a preexisting text file.

    One URL per line.
    Blank lines and lines beginning with # are ignored.
    """

    urls = []

    with open(filename, "r", encoding="utf-8") as file:

        for line in file:
            url = line.strip()

            if not url:
                continue

            if url.startswith("#"):
                continue

            normalized = normalize_url(url)

            if normalized is None:
                print(
                    f"WARNING: Skipping invalid URL: {url}"
                )
                continue

            urls.append(normalized)

    return urls

# ============================================================
# URL helpers
# ============================================================

def normalize_url(url):
    """
    Normalize a URL for crawling.

    - Only allows HTTP/HTTPS.
    - Restricts the crawler to ALLOWED_DOMAIN.
    - Removes fragments.
    - Removes query strings.
    - Removes duplicate slashes.
    - Removes trailing slashes except for the root URL.
    """

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    hostname = parsed.netloc.lower()

    # Remove default ports.
    hostname = hostname.replace(":443", "")
    hostname = hostname.replace(":80", "")

    if hostname != ALLOWED_DOMAIN:
        return None

    path = parsed.path or "/"

    # Remove duplicate slashes.
    path = re.sub(r"/{2,}", "/", path)

    # Normalize trailing slash.
    if path != "/":
        path = path.rstrip("/")

    return f"https://{ALLOWED_DOMAIN}{path}"


def is_internal_url(url):
    """
    Return True if URL belongs to the target domain.
    """

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.netloc.lower()

    hostname = hostname.replace(":443", "")
    hostname = hostname.replace(":80", "")

    return hostname == ALLOWED_DOMAIN


def should_crawl_url(url):
    """
    Return True if URL looks like an HTML page we should crawl.
    """

    if not is_internal_url(url):
        return False

    parsed = urlparse(url)

    suffix = Path(parsed.path.lower()).suffix

    if suffix in SKIP_EXTENSIONS:
        return False

    return True


def page_filename(url):
    """
    Convert URL into a safe Markdown filename.

    Examples:

        https://dnd5e.wikidot.com/
            -> index.md

        https://dnd5e.wikidot.com/spells
            -> spells.md

        https://dnd5e.wikidot.com/spell:fireball
            -> spell__fireball.md
    """

    parsed = urlparse(url)

    path = parsed.path.strip("/")

    if not path:
        return "index.md"

    # Preserve Wikidot's colon distinction.
    path = path.replace(":", "__")

    # Replace characters unsafe for filenames.
    path = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        path
    )

    # Remove control characters.
    path = re.sub(
        r"[\x00-\x1f]",
        "",
        path
    )

    # Normalize whitespace.
    path = re.sub(
        r"\s+",
        " ",
        path
    ).strip()

    if not path:
        path = "unnamed"

    return path[:200] + ".md"


# ============================================================
# Queue management
# ============================================================

def enqueue_url(url):
    """
    Add a URL to the download queue exactly once.

    queued_urls is deliberately NOT cleared after a URL is
    removed from the deque.

    This means a URL that has already been queued can never be
    queued again during this crawl, even if another page
    discovers it later.

    Returns:
        True  -> URL was added
        False -> URL was already queued/processed or invalid
    """

    normalized = normalize_url(url)

    if normalized is None:
        return False

    if not should_crawl_url(normalized):
        return False

    # --------------------------------------------------------
    # First deduplication check:
    #
    # Has this URL ever entered the download queue?
    # --------------------------------------------------------

    if normalized in queued_urls:
        return False

    # --------------------------------------------------------
    # Second deduplication check:
    #
    # Has this URL already been processed?
    # --------------------------------------------------------

    if normalized in processed_urls:
        return False

    # --------------------------------------------------------
    # Add to BOTH the permanent queued set and deque.
    # --------------------------------------------------------

    queued_urls.add(normalized)
    download_queue.append(normalized)

    return True


# ============================================================
# HTTP
# ============================================================

def get_page(url):
    """
    Download one page with retry handling.

    Returns:
        HTML string, or None on failure.
    """

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()

            # Only process HTML pages.
            if "text/html" not in content_type:
                return None

            return response.text

        except requests.RequestException as error:

            print(
                f"    Request failed "
                f"(attempt {attempt}/{MAX_RETRIES}): "
                f"{url} -> {error}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(3)

    return None


# ============================================================
# Link discovery
# ============================================================

def discover_urls(html, base_url):
    """
    Discover internal page URLs from downloaded HTML.
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    discovered = set()

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link["href"].strip()

        if not href:
            continue

        # Ignore JavaScript.
        if href.lower().startswith(
            "javascript:"
        ):
            continue

        # Ignore non-page schemes.
        if href.lower().startswith(
            (
                "mailto:",
                "tel:",
                "sms:",
                "data:",
            )
        ):
            continue

        # Resolve relative links.
        absolute_url = urljoin(
            base_url,
            href
        )

        # Remove #fragment.
        absolute_url, _ = urldefrag(
            absolute_url
        )

        normalized = normalize_url(
            absolute_url
        )

        if normalized is None:
            continue

        if not should_crawl_url(
            normalized
        ):
            continue

        discovered.add(normalized)

    return discovered


# ============================================================
# HTML -> Markdown extraction
# ============================================================

def extract_text(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    content = soup.select_one("#mw-content-text .mw-parser-output")

    if content is None:
        content = soup.select_one(".mw-parser-output")

    if content is None:
        return None

    if content is None:
        return None

    # --------------------------------------------------------
    # Remove unwanted elements
    # --------------------------------------------------------

    for element in content.select(
        "script, style, noscript, iframe"
    ):
        element.decompose()

    # --------------------------------------------------------
    # Headings
    # --------------------------------------------------------

    for level in range(1, 7):

        for tag in content.find_all(
            f"h{level}"
        ):

            text = tag.get_text(
                " ",
                strip=True
            )

            tag.replace_with(
                f"\n\n{'#' * level} {text}\n\n"
            )

    # --------------------------------------------------------
    # Paragraphs
    # --------------------------------------------------------

    for tag in content.find_all("p"):

        tag.insert_before("\n\n")
        tag.insert_after("\n\n")

    # --------------------------------------------------------
    # Line breaks
    # --------------------------------------------------------

    for tag in content.find_all("br"):
        tag.replace_with("\n")

    # --------------------------------------------------------
    # Bold
    # --------------------------------------------------------

    for tag in content.find_all(
        ["strong", "b"]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        tag.replace_with(
            f"**{text}**"
        )

    # --------------------------------------------------------
    # Italic
    # --------------------------------------------------------

    for tag in content.find_all(
        ["em", "i"]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        tag.replace_with(
            f"*{text}*"
        )

    # --------------------------------------------------------
    # Unordered lists
    # --------------------------------------------------------

    for ul in content.find_all("ul"):

        for li in ul.find_all(
            "li",
            recursive=False
        ):

            li.insert_before("\n- ")
            li.insert_after("\n")

    # --------------------------------------------------------
    # Ordered lists
    # --------------------------------------------------------

    for ol in content.find_all("ol"):

        for number, li in enumerate(
            ol.find_all(
                "li",
                recursive=False
            ),
            start=1
        ):

            li.insert_before(
                f"\n{number}. "
            )

            li.insert_after("\n")

    # --------------------------------------------------------
    # Tables
    # --------------------------------------------------------

    for table in content.find_all("table"):

        rows = []

        for tr in table.find_all("tr"):

            cells = tr.find_all(
                ["th", "td"],
                recursive=False
            )

            if not cells:
                continue

            row = []

            for cell in cells:

                text = cell.get_text(
                    " ",
                    strip=True
                )

                text = text.replace(
                    "|",
                    "\\|"
                )

                row.append(text)

            rows.append(row)

        if rows:

            columns = max(
                len(row)
                for row in rows
            )

            for row in rows:

                while len(row) < columns:
                    row.append("")

            markdown = []

            # Header
            markdown.append(
                "| "
                + " | ".join(rows[0])
                + " |"
            )

            # Separator
            markdown.append(
                "| "
                + " | ".join(
                    "---"
                    for _ in range(columns)
                )
                + " |"
            )

            # Remaining rows
            for row in rows[1:]:

                markdown.append(
                    "| "
                    + " | ".join(row)
                    + " |"
                )

            replacement = (
                "\n\n"
                + "\n".join(markdown)
                + "\n\n"
            )

            table.replace_with(
                replacement
            )

    # --------------------------------------------------------
    # Blockquotes
    # --------------------------------------------------------

    for tag in content.find_all(
        "blockquote"
    ):

        text = tag.get_text(
            "\n",
            strip=True
        )

        lines = text.splitlines()

        replacement = (
            "\n\n"
            + "\n".join(
                f"> {line}"
                for line in lines
                if line.strip()
            )
            + "\n\n"
        )

        tag.replace_with(
            replacement
        )

    # --------------------------------------------------------
    # Links
    # --------------------------------------------------------

    for tag in content.find_all("a"):

        text = tag.get_text(
            " ",
            strip=True
        )

        href = tag.get("href")

        if not text:
            continue

        if href:

            href = urljoin(
                BASE_URL,
                href
            )

            href, _ = urldefrag(href)

            tag.replace_with(
                f"[{text}]({href})"
            )

        else:

            tag.replace_with(text)

    # --------------------------------------------------------
    # Extract text
    # --------------------------------------------------------

    text = content.get_text(
        "",
        strip=False
    )

    # Normalize spaces without destroying
    # newlines.
    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    # Remove spaces around newlines.
    text = re.sub(
        r" *\n *",
        "\n",
        text
    )

    # Collapse excessive blank lines.
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


# ============================================================
# Save page
# ============================================================

def save_page(url, html):
    """
    Extract page content and save it as Markdown.

    Returns:
        (success, filename)
    """

    text = extract_text(html)

    if not text:
        return False, None

    filename = page_filename(url)

    output_file = OUTPUT_DIR / filename

    # Don't overwrite existing files.
    if output_file.exists():
        return True, filename

    output = (
        f"Source: {url}\n\n"
        f"{text}\n"
    )

    output_file.write_text(
        output,
        encoding="utf-8"
    )

    return True, filename


# ============================================================
# Main
# ============================================================

def main():

    global download_queue
    global queued_urls
    global processed_urls

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print("======================================")
    print("       Wikipedia Manifest Scraper")
    print("======================================")
    print()

    # --------------------------------------------------------
    # Load preexisting URL list.
    # --------------------------------------------------------

    print(
        f"Loading URL list: "
        f"{URL_LIST_FILE}"
    )

    urls = load_url_list(
        URL_LIST_FILE
    )

    print(
        f"Loaded {len(urls):,} URLs."
    )

    # --------------------------------------------------------
    # Initialize queue tracking.
    # --------------------------------------------------------

    download_queue = deque()

    queued_urls = set()

    processed_urls = set()

    failed_urls = []

    # --------------------------------------------------------
    # Put ONLY the URLs from the manifest into the queue.
    #
    # We deliberately do NOT discover links from pages.
    # --------------------------------------------------------

    for url in urls:
        enqueue_url(url)

    print(
        f"Queued {len(download_queue):,} URLs."
    )

    print()

    completed = 0

    # --------------------------------------------------------
    # Thread pool
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        while (
            download_queue
            or futures
        ):

            # ------------------------------------------------
            # Fill worker pool.
            # ------------------------------------------------

            while (
                download_queue
                and len(futures) < MAX_WORKERS
            ):

                url = download_queue.popleft()

                # This should normally be impossible because
                # enqueue_url() already prevents duplicates,
                # but keep this guard as an additional layer.
                if url in processed_urls:
                    continue

                future = executor.submit(
                    get_page,
                    url
                )

                futures[future] = url

            # ------------------------------------------------
            # Nothing currently running.
            # ------------------------------------------------

            if not futures:
                continue

            # ------------------------------------------------
            # Wait until at least one worker completes.
            # ------------------------------------------------

            done, _ = wait(
                futures,
                return_when=FIRST_COMPLETED
            )

            # ------------------------------------------------
            # Process completed downloads.
            # ------------------------------------------------

            for future in done:

                url = futures.pop(future)

                completed += 1

                try:

                    html = future.result()

                except Exception as error:

                    print(
                        f"[{completed:,}] "
                        f"ERROR downloading {url}: "
                        f"{error}"
                    )

                    failed_urls.append(url)

                    # Mark it processed so it cannot be
                    # accidentally requeued.
                    processed_urls.add(url)

                    continue

                # ------------------------------------------------
                # Download failed.
                # ------------------------------------------------

                if html is None:

                    print(
                        f"[{completed:,}] "
                        f"FAILED {url}"
                    )

                    failed_urls.append(url)

                    processed_urls.add(url)

                    continue

                # ------------------------------------------------
                # Discover links.
                # ------------------------------------------------

                
                new_urls = 0
                # ------------------------------------------------
                # Save page.
                # ------------------------------------------------

                try:

                    success, filename = save_page(
                        url,
                        html
                    )

                    if success:

                        print(
                            f"[{completed:,}] "
                            f"SAVED {filename} "
                            f"(+{new_urls} new URLs)"
                        )

                    else:

                        print(
                            f"[{completed:,}] "
                            f"NO CONTENT {url} "
                            f"(+{new_urls} new URLs)"
                        )

                        failed_urls.append(url)

                except Exception as error:

                    print(
                        f"[{completed:,}] "
                        f"ERROR processing {url}: "
                        f"{error}"
                    )

                    failed_urls.append(url)

                finally:

                    # ------------------------------------------------
                    # This URL is now permanently processed.
                    # ------------------------------------------------

                    processed_urls.add(url)

    # ========================================================
    # Failure log
    # ========================================================

    unique_failed = sorted(
        set(failed_urls)
    )

    if unique_failed:

        failure_file = (
            OUTPUT_DIR / "failed.txt"
        )

        failure_file.write_text(
            "\n".join(unique_failed),
            encoding="utf-8"
        )

        print()
        print(
            f"{len(unique_failed):,} pages failed."
        )

        print(
            f"Failure log: "
            f"{failure_file.resolve()}"
        )

    # ========================================================
    # Finished
    # ========================================================

    print()
    print("======================================")
    print(" Finished")
    print("======================================")

    print(
        f"URLs ever queued:   "
        f"{len(queued_urls):,}"
    )

    print(
        f"URLs processed:     "
        f"{len(processed_urls):,}"
    )

    print(
        f"URLs failed:        "
        f"{len(unique_failed):,}"
    )

    print(
        f"Output:             "
        f"{OUTPUT_DIR.resolve()}"
    )

    print()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()