#!/usr/bin/env python3

import re
import sqlite3
import time
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

INPUT_DIR = Path("WikiMillion")
DATABASE_FILE = Path("wikipedia.db")

BATCH_SIZE = 500


# ============================================================
# Database setup
# ============================================================

def create_database(connection):
    """
    Create the database tables and indexes if they do not exist.
    """

    connection.executescript(
        """
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL UNIQUE,
            text TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts
        USING fts5(
            title,
            text,
            content='articles',
            content_rowid='id',
            tokenize='unicode61'
        );

        CREATE TABLE IF NOT EXISTS indexed_files (
            filename TEXT PRIMARY KEY,
            indexed_at REAL NOT NULL
        );
        """
    )

    connection.commit()


# ============================================================
# Article parsing
# ============================================================

def extract_article(path):
    """
    Read one Markdown file from the scraper.

    Expected format:

        Source: https://en.wikipedia.org/wiki/Example

        Article text...
    """

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"    ERROR reading {path}: {error}")
        return None

    lines = content.splitlines()

    if not lines:
        return None

    # --------------------------------------------------------
    # Extract source URL.
    # --------------------------------------------------------

    url = None

    if lines[0].startswith("Source: "):
        url = lines[0][len("Source: "):].strip()

    if not url:
        print(f"    WARNING: No Source URL in {path}")
        return None

    # --------------------------------------------------------
    # Everything after the first blank line is article text.
    # --------------------------------------------------------

    separator = content.find("\n\n")

    if separator >= 0:
        text = content[separator + 2:]
    else:
        text = ""

    text = text.strip()

    if not text:
        print(f"    WARNING: Empty article: {path}")
        return None

    # --------------------------------------------------------
    # Determine title.
    #
    # Wikipedia filenames are based on the URL path, so this
    # gives us a reasonable title even if the Markdown itself
    # does not contain a top-level heading.
    # --------------------------------------------------------

    title = path.stem

    # The scraper uses "__" in place of ":".
    title = title.replace("__", ":")

    # Convert underscores back into spaces.
    title = title.replace("_", " ")

    # Decode a few common URL-ish representations.
    title = re.sub(r"\s+", " ", title).strip()

    if not title:
        title = "Untitled"

    return title, url, path.name, text


# ============================================================
# FTS maintenance
# ============================================================

def rebuild_fts(connection):
    """
    Rebuild the FTS index from the articles table.

    This is used at the end to make absolutely sure the external
    content FTS table matches the article database.
    """

    connection.execute(
        "INSERT INTO articles_fts(articles_fts) VALUES ('rebuild')"
    )

    connection.commit()


# ============================================================
# Main indexing process
# ============================================================

def main():

    print()
    print("======================================")
    print("       Wikipedia SQLite Indexer")
    print("======================================")
    print()

    if not INPUT_DIR.exists():
        print(f"ERROR: Input directory does not exist:")
        print(f"    {INPUT_DIR.resolve()}")
        return

    print(f"Input:    {INPUT_DIR.resolve()}")
    print(f"Database: {DATABASE_FILE.resolve()}")
    print()

    # --------------------------------------------------------
    # Find Markdown files.
    # --------------------------------------------------------

    print("Finding articles...")

    files = list(INPUT_DIR.glob("*.md"))

    # Don't accidentally index the failure log.
    files = [
        path for path in files
        if path.name != "failed.txt"
    ]

    files.sort()

    print(f"Found {len(files):,} Markdown files.")
    print()

    # --------------------------------------------------------
    # Open database.
    # --------------------------------------------------------

    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=60
    )

    try:
        create_database(connection)

        # ----------------------------------------------------
        # Determine which files have already been indexed.
        # ----------------------------------------------------

        indexed = {
            row[0]
            for row in connection.execute(
                "SELECT filename FROM indexed_files"
            )
        }

        remaining = [
            path
            for path in files
            if path.name not in indexed
        ]

        print(
            f"Already indexed: {len(indexed):,}"
        )

        print(
            f"Remaining:       {len(remaining):,}"
        )

        print()

        if not remaining:
            print("Nothing new to index.")
            print("Rebuilding FTS index...")
            rebuild_fts(connection)
            print("Done.")
            return

        # ----------------------------------------------------
        # Index articles in batches.
        # ----------------------------------------------------

        start_time = time.time()

        batch = []

        indexed_count = 0
        failed_count = 0

        for number, path in enumerate(
            remaining,
            start=1
        ):

            article = extract_article(path)

            if article is None:
                failed_count += 1
                continue

            title, url, filename, text = article

            batch.append(
                (
                    title,
                    url,
                    filename,
                    text
                )
            )

            # ------------------------------------------------
            # Write batch.
            # ------------------------------------------------

            if len(batch) >= BATCH_SIZE:

                connection.executemany(
                    """
                    INSERT OR IGNORE INTO articles
                    (title, url, filename, text)
                    VALUES (?, ?, ?, ?)
                    """,
                    batch
                )

                connection.executemany(
                    """
                    INSERT OR IGNORE INTO indexed_files
                    (filename, indexed_at)
                    VALUES (?, ?)
                    """,
                    [
                        (article[2], time.time())
                        for article in batch
                    ]
                )

                connection.commit()

                indexed_count += len(batch)
                batch.clear()

                # --------------------------------------------
                # Progress.
                # --------------------------------------------

                elapsed = time.time() - start_time

                rate = (
                    indexed_count / elapsed
                    if elapsed > 0
                    else 0
                )

                if rate > 0:
                    remaining_seconds = (
                        len(remaining) - number
                    ) / rate
                else:
                    remaining_seconds = 0

                percent = (
                    number / len(remaining) * 100
                    if remaining
                    else 100
                )

                print(
                    f"[{number:,}/{len(remaining):,}] "
                    f"{percent:6.2f}% | "
                    f"{indexed_count:,} indexed | "
                    f"{rate:,.1f}/sec | "
                    f"ETA {format_duration(remaining_seconds)}"
                )

        # ----------------------------------------------------
        # Write final partial batch.
        # ----------------------------------------------------

        if batch:

            connection.executemany(
                """
                INSERT OR IGNORE INTO articles
                (title, url, filename, text)
                VALUES (?, ?, ?, ?)
                """,
                batch
            )

            connection.executemany(
                """
                INSERT OR IGNORE INTO indexed_files
                (filename, indexed_at)
                VALUES (?, ?)
                """,
                [
                    (article[2], time.time())
                    for article in batch
                ]
            )

            connection.commit()

            indexed_count += len(batch)

        print()
        print("Articles loaded into database.")
        print(f"Newly indexed: {indexed_count:,}")
        print(f"Failed:        {failed_count:,}")
        print()

        # ----------------------------------------------------
        # Build FTS index.
        # ----------------------------------------------------

        print("Building full-text search index...")
        print("(This may take a while.)")

        rebuild_fts(connection)

        print("FTS index complete.")
        print()

        # ----------------------------------------------------
        # Optimize.
        # ----------------------------------------------------

        print("Optimizing database...")

        connection.execute(
            "PRAGMA optimize"
        )

        connection.commit()

        print("Done.")
        print()

        # ----------------------------------------------------
        # Statistics.
        # ----------------------------------------------------

        article_count = connection.execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()[0]

        database_size = (
            DATABASE_FILE.stat().st_size
            / (1024 ** 3)
        )

        print("======================================")
        print(" Finished")
        print("======================================")
        print()
        print(
            f"Articles in database: {article_count:,}"
        )
        print(
            f"Database size:        {database_size:.2f} GiB"
        )
        print(
            f"Database:             {DATABASE_FILE.resolve()}"
        )
        print()

    finally:
        connection.close()


# ============================================================
# Utilities
# ============================================================

def format_duration(seconds):

    seconds = max(0, int(seconds))

    hours, remainder = divmod(
        seconds,
        3600
    )

    minutes, seconds = divmod(
        remainder,
        60
    )

    if hours:
        return f"{hours}h {minutes:02d}m"

    if minutes:
        return f"{minutes}m {seconds:02d}s"

    return f"{seconds}s"


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()