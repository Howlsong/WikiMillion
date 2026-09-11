#!/usr/bin/env python3

"""
Build a ranked list of the 1,000,000 most important/popular
English Wikipedia articles based on monthly top-1000 rankings.

For every month in the selected date range:
    - Query Wikimedia's pageviews/top endpoint.
    - Take the top 1,000 articles.
    - Award rank-based points:
        #1    = 1000 points
        #2    =  999 points
        ...
        #1000 = 1 point
    - Add those points to each article's cumulative score.

At the end:
    - Sort articles by cumulative score.
    - Take the top 1,000,000.
    - Write their Wikipedia URLs to a TXT file.

The monthly API responses are cached so the script can be
restarted without re-downloading completed months.
"""

import json
import time
from collections import defaultdict
from datetime import date

import requests


# ============================================================
# Configuration
# ============================================================

PROJECT = "en.wikipedia.org"
ACCESS = "all-access"

TOP_N_PER_MONTH = 1000
FINAL_N = 1_000_000

# Wikipedia pageview data begins in July 2015.
START_YEAR = 2015
START_MONTH = 7

# None = automatically use the most recently completed month.
END_YEAR = None
END_MONTH = None

CACHE_DIR = "wikipedia_monthly_cache"

OUTPUT_FILE = "wikipedia_top_1m_urls.txt"

REQUEST_TIMEOUT = 60
MAX_RETRIES = 5
RETRY_DELAY = 5

USER_AGENT = (
    "WikipediaTopMillionCollector/1.0 "
    "(personal offline archive project)"
)


# ============================================================
# HTTP session
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})


# ============================================================
# Date helpers
# ============================================================

def month_sequence(start_year, start_month, end_year, end_month):
    """
    Yield (year, month) tuples from start through end inclusive.
    """

    year = start_year
    month = start_month

    while (year, month) <= (end_year, end_month):
        yield year, month

        month += 1

        if month > 12:
            month = 1
            year += 1


def last_day_of_month(year, month):
    """
    Return the final calendar day of a month.
    """

    if month == 12:
        return 31

    next_month = date(year, month + 1, 1)

    return (
        next_month
        - __import__("datetime").timedelta(days=1)
    ).day


def determine_end_month():
    """
    Determine the most recently completed calendar month.
    """

    today = date.today()

    if today.month == 1:
        return today.year - 1, 12

    return today.year, today.month - 1


# ============================================================
# Cache helpers
# ============================================================

def cache_filename(year, month):
    return (
        f"{CACHE_DIR}/"
        f"{year:04d}-{month:02d}.json"
    )


def load_cached_month(year, month):
    """
    Load a previously downloaded monthly response.
    """

    filename = cache_filename(year, month)

    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)

    except FileNotFoundError:
        return None


def save_cached_month(year, month, data):
    """
    Save a monthly response to disk.
    """

    import os

    os.makedirs(CACHE_DIR, exist_ok=True)

    filename = cache_filename(year, month)

    temporary_filename = filename + ".tmp"

    with open(
        temporary_filename,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
        )

    os.replace(
        temporary_filename,
        filename,
    )


# ============================================================
# Wikimedia API
# ============================================================

def get_monthly_top(year, month):
    """
    Get the top 1000 Wikipedia pages for an entire month.

    A 404 means Wikimedia does not have data for that particular
    month. That is NOT fatal to the overall ranking, so we skip
    that month and return None.

    Other HTTP failures are retried.
    """

    cached = load_cached_month(year, month)

    if cached is not None:
        print(
            f"[{year:04d}-{month:02d}] "
            f"Using cached data."
        )

        return cached

    url = (
        "https://wikimedia.org/api/rest_v1/"
        "metrics/pageviews/top/"
        f"{PROJECT}/"
        f"{ACCESS}/"
        f"{year:04d}/"
        f"{month:02d}/"
        "all-days"
    )

    print(
        f"[{year:04d}-{month:02d}] "
        f"Downloading..."
    )

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            # ------------------------------------------------
            # 404 = no data for this month.
            #
            # Do NOT retry it. Retrying a missing month just
            # wastes time and gets us nowhere.
            # ------------------------------------------------

            if response.status_code == 404:

                print(
                    f"    NO DATA for "
                    f"{year:04d}-{month:02d} "
                    f"(HTTP 404) — skipping month."
                )

                return None

            response.raise_for_status()

            data = response.json()

            save_cached_month(
                year,
                month,
                data,
            )

            return data

        except requests.RequestException as error:

            print(
                f"    Attempt {attempt}/"
                f"{MAX_RETRIES} failed: {error}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

        except ValueError as error:

            print(
                f"    Invalid JSON response for "
                f"{year:04d}-{month:02d}: "
                f"{error}"
            )

            return None

    print(
        f"    FAILED {year:04d}-{month:02d} "
        f"after {MAX_RETRIES} attempts."
    )

    return None


# ============================================================
# Extract top 1000 articles
# ============================================================

def extract_top_articles(data):
    """
    Extract Wikipedia article titles from a Wikimedia
    pageviews/top response.

    Wikimedia returns multiple project categories in
    the response. We only want the normal 'articles'
    category.
    """

    items = data.get("items", [])

    if not items:
        return []

    # The usual structure is:
    #
    # items[0]["articles"] = [...]
    #
    # Be slightly defensive in case the response changes.

    articles = []

    for item in items:

        if "articles" in item:
            articles.extend(
                item["articles"]
            )

    return articles[:TOP_N_PER_MONTH]


# ============================================================
# Title filtering
# ============================================================

def is_valid_article(article):
    """
    Decide whether an entry should count as an ordinary
    Wikipedia article.

    We intentionally exclude:
        - Main Page
        - non-main namespaces
        - special/internal pages
    """

    article_name = article.get("article")

    if not article_name:
        return False

    # Main Page is not useful as an encyclopedia article.
    if article_name == "Main_Page":
        return False

    # Wikimedia uses underscores in the API.
    #
    # Namespace prefixes that should not enter the archive.
    excluded_prefixes = (
        "Special:",
        "Wikipedia:",
        "Talk:",
        "User:",
        "User_talk:",
        "Template:",
        "Template_talk:",
        "File:",
        "File_talk:",
        "MediaWiki:",
        "MediaWiki_talk:",
        "Category:",
        "Category_talk:",
        "Portal:",
        "Portal_talk:",
        "Book:",
        "Book_talk:",
        "Draft:",
        "Draft_talk:",
        "Module:",
        "Module_talk:",
        "Help:",
        "Help_talk:",
        "TimedText:",
        "TimedText_talk:",
    )

    decoded = article_name.replace(
        "_",
        " ",
    )

    for prefix in excluded_prefixes:

        if decoded.startswith(prefix):
            return False

    return True


# ============================================================
# Ranking
# ============================================================

def build_rankings(start_year, start_month, end_year, end_month):
    """
    Accumulate rank-based importance scores.

    Rank 1    -> 1000 points
    Rank 2    -> 999 points
    ...
    Rank 1000 -> 1 point
    """

    scores = defaultdict(int)

    months_processed = 0

    months = list(
        month_sequence(
            start_year,
            start_month,
            end_year,
            end_month,
        )
    )

    total_months = len(months)

    for index, (year, month) in enumerate(
        months,
        start=1,
    ):

        data = get_monthly_top(
            year,
            month,
        )

        if data is None:
            print(
                f"    Skipping {year:04d}-{month:02d}."
            )
            continue

        articles = extract_top_articles(
            data
        )

        valid_rank = 0

        for article in articles:

            if not is_valid_article(article):
                continue

            title = article["article"]

            valid_rank += 1

            # Rank-based scoring.
            #
            # First valid article:
            #     1000 points
            #
            # 1000th valid article:
            #     1 point
            score = (
                TOP_N_PER_MONTH
                - valid_rank
                + 1
            )

            scores[title] += score

        months_processed += 1

        print(
            f"    Processed "
            f"{len(articles):,} entries; "
            f"{valid_rank:,} valid articles."
        )

        print(
            f"    Progress: "
            f"{index}/{total_months} months; "
            f"{len(scores):,} unique articles."
        )

        # Be polite to the API.
        time.sleep(1)

    return scores


# ============================================================
# URL conversion
# ============================================================

def title_to_url(title):
    """
    Convert an API article title into its canonical
    Wikipedia URL.

    The API generally returns underscores, which are
    perfectly valid in Wikipedia URLs.
    """

    from urllib.parse import quote

    return (
        "https://en.wikipedia.org/wiki/"
        + quote(
            title.replace(
                " ",
                "_",
            ),
            safe="_:()!,",
        )
    )


# ============================================================
# Output
# ============================================================

def write_urls(scores):
    """
    Sort articles by cumulative importance score and
    write the top FINAL_N URLs to the output file.
    """

    print()
    print(
        f"Sorting {len(scores):,} "
        f"unique articles..."
    )

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            item[0].lower(),
        ),
    )

    selected = ranked[:FINAL_N]

    print(
        f"Selected {len(selected):,} articles."
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        for title, score in selected:

            file.write(
                title_to_url(title)
                + "\n"
            )

    print()
    print(
        f"Wrote URLs to: "
        f"{OUTPUT_FILE}"
    )

    return selected


# ============================================================
# Optional ranking report
# ============================================================

def print_preview(ranked):
    """
    Show the first few entries so you can sanity-check
    the resulting ranking.
    """

    print()
    print("Top 25:")
    print("----------------------------------------")

    for position, (title, score) in enumerate(
        ranked[:25],
        start=1,
    ):

        print(
            f"{position:6,}  "
            f"{score:10,}  "
            f"{title}"
        )

    print("----------------------------------------")


# ============================================================
# Main
# ============================================================

def main():

    print()
    print("==============================================")
    print("       Wikipedia Top Million Collector")
    print("==============================================")
    print()

    if END_YEAR is None or END_MONTH is None:
        end_year, end_month = determine_end_month()
    else:
        end_year = END_YEAR
        end_month = END_MONTH

    print(
        f"Date range: "
        f"{START_YEAR:04d}-{START_MONTH:02d} "
        f"through "
        f"{end_year:04d}-{end_month:02d}"
    )

    print(
        f"Monthly sample: "
        f"top {TOP_N_PER_MONTH:,}"
    )

    print(
        f"Final archive: "
        f"top {FINAL_N:,}"
    )

    print()
    print(
        "Rank scoring:"
    )
    print(
        "    #1     = 1000 points"
    )
    print(
        "    #1000  = 1 point"
    )
    print()

    scores = build_rankings(
        START_YEAR,
        START_MONTH,
        end_year,
        end_month,
    )

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            item[0].lower(),
        ),
    )

    print_preview(ranked)

    write_urls(scores)

    print()
    print("==============================================")
    print("                    Done")
    print("==============================================")
    print()


if __name__ == "__main__":
    main()