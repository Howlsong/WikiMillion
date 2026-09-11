#!/usr/bin/env python3

import html
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


# ============================================================
# Configuration
# ============================================================

DATABASE_FILE = Path("wikipedia.db")

HOST = "127.0.0.1"
PORT = 8080

RESULTS_PER_PAGE = 25


# ============================================================
# Database
# ============================================================

def get_connection():
    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# HTML helpers
# ============================================================

def escape(value):
    return html.escape(
        str(value),
        quote=True
    )


def page_template(title, body):

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,
             initial-scale=1"
>

<title>{escape(title)} — Offline Wikipedia</title>

<style>

:root {{
    color-scheme: light;

    --background: #ffffff;
    --foreground: #202122;
    --muted: #54595d;
    --link: #0645ad;
    --border: #a2a9b1;
    --input-background: #ffffff;
    --button-background: #f8f9fa;
    --table-header: #eaecf0;
    --table-stripe: #f8f9fa;
    --code-background: #f8f9fa;
    --mark-background: #ffeb3b;
}}

html.dark {{
    color-scheme: dark;

    --background: #181a1b;
    --foreground: #e8e6e3;
    --muted: #b8b5b1;
    --link: #6ea8fe;
    --border: #666;
    --input-background: #242627;
    --button-background: #2d2f30;
    --table-header: #303335;
    --table-stripe: #222425;
    --code-background: #242627;
    --mark-background: #665c00;
}}

html,
body {{
    background: var(--background);
    color: var(--foreground);
}}

body {{
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    max-width: 1000px;
    margin: 0 auto;
    padding: 1rem;

    line-height: 1.6;
}}

a {{
    color: var(--link);
}}

.search {{
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
}}

.search form {{
    display: flex;
    width: 100%;
    gap: 0.5rem;
}}

.search input {{
    flex: 1;
    min-width: 0;

    font-size: 1rem;
    padding: 0.7rem;

    color: var(--foreground);
    background: var(--input-background);

    border: 1px solid var(--border);
    border-radius: 6px;
}}

.search button,
.theme-button {{
    font-size: 1rem;
    padding: 0.7rem 1rem;

    color: var(--foreground);
    background: var(--button-background);

    border: 1px solid var(--border);
    border-radius: 6px;

    cursor: pointer;
}}

.search button:hover,
.theme-button:hover {{
    filter: brightness(0.95);
}}

.theme-button {{
    flex-shrink: 0;
}}

.result {{
    margin-bottom: 1.5rem;
}}

.result h2 {{
    margin-bottom: 0.2rem;
}}

.result p {{
    margin-top: 0.2rem;
}}

.article {{
    max-width: 850px;
}}

.article img {{
    max-width: 100%;
    height: auto;
}}

pre {{
    overflow-x: auto;
    padding: 1rem;

    background: var(--code-background);

    border: 1px solid var(--border);
    border-radius: 6px;
}}

code {{
    font-family:
        ui-monospace,
        SFMono-Regular,
        Consolas,
        "Liberation Mono",
        monospace;
}}

blockquote {{
    margin-left: 0;
    padding-left: 1rem;

    border-left: 4px solid var(--border);
    color: var(--muted);
}}

hr {{
    border: 0;
    border-top: 1px solid var(--border);
}}

.table-wrapper {{
    overflow-x: auto;
    margin: 1rem 0;
}}

table {{
    border-collapse: collapse;
    width: max-content;
    max-width: 100%;
}}

th,
td {{
    border: 1px solid var(--border);
    padding: 0.4rem 0.7rem;
    text-align: left;
    vertical-align: top;
}}

th {{
    background: var(--table-header);
    font-weight: 600;
}}

tr:nth-child(even) td {{
    background: var(--table-stripe);
}}

mark {{
    background: var(--mark-background);
    color: inherit;
}}

@media (max-width: 600px) {{

    body {{
        padding: 0.75rem;
    }}

    .search form {{
        flex-wrap: wrap;
    }}

    .search input {{
        flex-basis: 100%;
    }}

}}

</style>

<script>

(function() {{

    const savedTheme =
        localStorage.getItem("wiki-theme");

    if (savedTheme === "dark") {{
        document.documentElement.classList.add("dark");
    }}

}})();

function toggleTheme() {{

    const html =
        document.documentElement;

    const isDark =
        html.classList.toggle("dark");

    localStorage.setItem(
        "wiki-theme",
        isDark ? "dark" : "light"
    );

}}

</script>

</head>

<body>

<div class="search">

<form
    action="/"
    method="get"
>

<input
    type="search"
    name="q"
    placeholder="Search Wikipedia..."
    value="{escape(title if title != 'Offline Wikipedia' else '')}"
    autofocus
>

<button type="submit">
Search
</button>

<button
    type="button"
    class="theme-button"
    onclick="toggleTheme()"
    title="Toggle dark mode"
>
Theme
</button>

</form>

</div>

{body}

</body>
</html>
"""


# ============================================================
# Markdown rendering
# ============================================================

def markdown_to_html(markdown):

    text = markdown

    # --------------------------------------------------------
    # Escape raw HTML first.
    # --------------------------------------------------------

    text = escape(text)

    # --------------------------------------------------------
    # Code blocks.
    # --------------------------------------------------------

    code_blocks = []

    def save_code(match):

        index = len(code_blocks)

        code = match.group(1)

        code_blocks.append(
            "<pre><code>"
            + code
            + "</code></pre>"
        )

        return f"\n\n@@CODE{index}@@\n\n"

    text = re.sub(
        r"```(?:[^\n]*)\n(.*?)```",
        save_code,
        text,
        flags=re.DOTALL
    )

    # --------------------------------------------------------
    # Images.
    # --------------------------------------------------------

    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        r'<img src="\2" alt="\1">',
        text
    )

    # --------------------------------------------------------
    # Links.
    # --------------------------------------------------------

    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text
    )

    # --------------------------------------------------------
    # Headings.
    # --------------------------------------------------------

    for level in range(6, 0, -1):

        hashes = "#" * level

        text = re.sub(
            rf"^{re.escape(hashes)} (.+)$",
            rf"<h{level}>\1</h{level}>",
            text,
            flags=re.MULTILINE
        )

    # --------------------------------------------------------
    # Bold / italic.
    # --------------------------------------------------------

    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        text
    )

    text = re.sub(
        r"\*([^*\n]+)\*",
        r"<em>\1</em>",
        text
    )

    # --------------------------------------------------------
    # Horizontal rules.
    # --------------------------------------------------------

    text = re.sub(
        r"^---+$",
        "<hr>",
        text,
        flags=re.MULTILINE
    )

    # --------------------------------------------------------
    # Tables.
    #
    # Markdown tables look like:
    #
    # | Name | Value |
    # |------|-------|
    # | Foo  | 123   |
    #
    # Convert consecutive table lines into a real HTML table.
    # --------------------------------------------------------

    lines = text.splitlines()

    table_output = []

    index = 0

    def is_table_row(line):

        stripped = line.strip()

        return (
            "|" in stripped
            and stripped.count("|") >= 1
        )

    def split_table_row(line):

        line = line.strip()

        if line.startswith("|"):
            line = line[1:]

        if line.endswith("|"):
            line = line[:-1]

        return [
            cell.strip()
            for cell in line.split("|")
        ]

    def is_separator_row(line):

        cells = split_table_row(line)

        if not cells:
            return False

        return all(
            re.fullmatch(
                r":?-{3,}:?",
                cell
            )
            for cell in cells
        )

    while index < len(lines):

        line = lines[index]

        # A table requires a row followed by a separator row.
        if (
            is_table_row(line)
            and index + 1 < len(lines)
            and is_separator_row(lines[index + 1])
        ):

            header_cells = split_table_row(line)

            index += 2

            rows = []

            while (
                index < len(lines)
                and is_table_row(lines[index])
                and lines[index].strip()
            ):

                rows.append(
                    split_table_row(lines[index])
                )

                index += 1

            table = [
                '<div class="table-wrapper">',
                "<table>",
                "<thead>",
                "<tr>"
            ]

            for cell in header_cells:

                table.append(
                    "<th>"
                    + cell
                    + "</th>"
                )

            table.extend([
                "</tr>",
                "</thead>",
                "<tbody>"
            ])

            for row in rows:

                table.append("<tr>")

                # Pad short rows so malformed/incomplete
                # Wikipedia tables don't break the HTML.

                if len(row) < len(header_cells):

                    row = row + [
                        ""
                    ] * (
                        len(header_cells) - len(row)
                    )

                for cell in row[:len(header_cells)]:

                    table.append(
                        "<td>"
                        + cell
                        + "</td>"
                    )

                table.append("</tr>")

            table.extend([
                "</tbody>",
                "</table>",
                "</div>"
            ])

            table_output.append(
                "\n".join(table)
            )

            continue

        table_output.append(line)

        index += 1

    text = "\n".join(table_output)

    # --------------------------------------------------------
    # Unordered / ordered lists.
    # --------------------------------------------------------

    lines = text.splitlines()

    output = []

    in_ul = False
    in_ol = False

    for line in lines:

        if re.match(
            r"^\s*-\s+",
            line
        ):

            if not in_ul:
                output.append("<ul>")
                in_ul = True

            output.append(
                "<li>"
                + re.sub(
                    r"^\s*-\s+",
                    "",
                    line
                )
                + "</li>"
            )

            continue

        if re.match(
            r"^\s*\d+\.\s+",
            line
        ):

            if not in_ol:
                output.append("<ol>")
                in_ol = True

            output.append(
                "<li>"
                + re.sub(
                    r"^\s*\d+\.\s+",
                    "",
                    line
                )
                + "</li>"
            )

            continue

        if in_ul:
            output.append("</ul>")
            in_ul = False

        if in_ol:
            output.append("</ol>")
            in_ol = False

        output.append(line)

    if in_ul:
        output.append("</ul>")

    if in_ol:
        output.append("</ol>")

    text = "\n".join(output)

    # --------------------------------------------------------
    # Blockquotes.
    # --------------------------------------------------------

    text = re.sub(
        r"(?:^&gt; .+(?:\n|$))+",
        lambda match:
            "<blockquote>"
            + match.group(0).replace(
                "&gt; ",
                ""
            ).replace(
                "\n",
                "<br>\n"
            )
            + "</blockquote>",
        text,
        flags=re.MULTILINE
    )

    # --------------------------------------------------------
    # Paragraphs / line breaks.
    # --------------------------------------------------------

    blocks = re.split(
        r"\n\s*\n",
        text
    )

    rendered = []

    for block in blocks:

        block = block.strip()

        if not block:
            continue

        if (
            block.startswith("<h")
            or block.startswith("<ul>")
            or block.startswith("<ol>")
            or block.startswith("<blockquote>")
            or block.startswith("<pre>")
            or block.startswith("<hr>")
            or block.startswith(
                '<div class="table-wrapper">'
            )
            or "@@CODE" in block
        ):
            rendered.append(block)

        else:

            rendered.append(
                "<p>"
                + block.replace(
                    "\n",
                    "<br>\n"
                )
                + "</p>"
            )

    text = "\n\n".join(rendered)

    # --------------------------------------------------------
    # Restore code blocks.
    # --------------------------------------------------------

    for index, block in enumerate(code_blocks):

        text = text.replace(
            f"@@CODE{index}@@",
            block
        )

    return text


# ============================================================
# Search
# ============================================================

def search_articles(query):

    connection = get_connection()

    try:

        # ----------------------------------------------------
        # FTS5 query.
        #
        # We quote the user's terms so punctuation doesn't
        # accidentally become FTS operators.
        # ----------------------------------------------------

        terms = re.findall(
            r'\w+',
            query,
            flags=re.UNICODE
        )

        if not terms:
            return []

        fts_query = " ".join(
            f'"{term}"'
            for term in terms
        )

        rows = connection.execute(
            """
            SELECT
                articles.id,
                articles.title,
                articles.url,
                articles.filename,

                snippet(
                    articles_fts,
                    1,
                    '<mark>',
                    '</mark>',
                    ' … ',
                    40
                ) AS snippet,

                bm25(
                    articles_fts,
                    10.0,
                    1.0
                ) AS rank

            FROM articles_fts

            JOIN articles
                ON articles.id = articles_fts.rowid

            WHERE articles_fts MATCH ?

            ORDER BY rank

            LIMIT ?
            """,
            (
                fts_query,
                RESULTS_PER_PAGE
            )
        ).fetchall()

        return rows

    finally:

        connection.close()


# ============================================================
# Request handler
# ============================================================

class WikiHandler(BaseHTTPRequestHandler):

    def log_message(
        self,
        format_string,
        *args
    ):

        print(
            f"{self.address_string()} - "
            f"{format_string % args}"
        )

    # --------------------------------------------------------
    # Send response.
    # --------------------------------------------------------

    def send_html(
        self,
        content,
        status=200
    ):

        data = content.encode(
            "utf-8"
        )

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(data))
        )

        self.end_headers()

        self.wfile.write(data)

    # --------------------------------------------------------
    # GET.
    # --------------------------------------------------------

    def do_GET(self):

        parsed = urlparse(
            self.path
        )

        path = parsed.path

        params = parse_qs(
            parsed.query
        )

        # ----------------------------------------------------
        # Article.
        # ----------------------------------------------------

        if path.startswith("/article/"):

            filename = unquote(
                path[len("/article/"):]
            )

            self.show_article(
                filename
            )

            return

        # ----------------------------------------------------
        # Search page.
        # ----------------------------------------------------

        query = params.get(
            "q",
            [""]
        )[0].strip()

        if query:

            self.show_search(
                query
            )

        else:

            body = """
<h1>Offline Wikipedia</h1>

<p>
Your offline Wikipedia archive.
</p>

<p>
Enter a search above to find an article.
</p>
"""

            self.send_html(
                page_template(
                    "Offline Wikipedia",
                    body
                )
            )

    # --------------------------------------------------------
    # Search results.
    # --------------------------------------------------------

    def show_search(self, query):

        try:

            rows = search_articles(
                query
            )

        except sqlite3.Error as error:

            self.send_html(
                page_template(
                    "Search error",
                    f"""
<h1>Search error</h1>
<pre>{escape(error)}</pre>
"""
                ),
                status=500
            )

            return

        body = (
            f"<h1>Search results</h1>"
            f"<p>"
            f"{len(rows):,} result(s) shown for "
            f"<strong>{escape(query)}</strong>."
            f"</p>"
        )

        if not rows:

            body += """
<p>
No matching articles found.
</p>
"""

        else:

            for row in rows:

                article_url = (
                    "/article/"
                    + quote(
                        row["filename"]
                    )
                )

                body += f"""
<div class="result">

<h2>
<a href="{article_url}">
{escape(row["title"])}
</a>
</h2>

<p>
{row["snippet"] or ""}
</p>

</div>
"""

        self.send_html(
            page_template(
                query,
                body
            )
        )

    # --------------------------------------------------------
    # Article page.
    # --------------------------------------------------------

    def show_article(self, filename):

        connection = get_connection()

        try:

            row = connection.execute(
                """
                SELECT
                    title,
                    url,
                    text
                FROM articles
                WHERE filename = ?
                """,
                (filename,)
            ).fetchone()

        finally:

            connection.close()

        if row is None:

            self.send_html(
                page_template(
                    "Not found",
                    """
<h1>Article not found</h1>
<p>
That article does not exist in this archive.
</p>
"""
                ),
                status=404
            )

            return

        article_html = markdown_to_html(
            row["text"]
        )

        body = f"""
<div class="article">

<h1>{escape(row["title"])}</h1>

<p>
<a href="{escape(row["url"])}">
Original Wikipedia URL
</a>
</p>

<hr>

{article_html}

</div>
"""

        self.send_html(
            page_template(
                row["title"],
                body
            )
        )


# ============================================================
# Main
# ============================================================

def main():

    if not DATABASE_FILE.exists():

        print(
            "ERROR: Database does not exist:"
        )

        print(
            f"    {DATABASE_FILE.resolve()}"
        )

        print()

        print(
            "Run build_index.py after the scraper "
            "has finished."
        )

        return

    server = ThreadingHTTPServer(
        (HOST, PORT),
        WikiHandler
    )

    print()
    print("======================================")
    print("       Offline Wikipedia Server")
    print("======================================")
    print()

    print(
        f"Listening on http://{HOST}:{PORT}/"
    )

    print()

    print(
        "Press Ctrl+C to stop."
    )

    print()

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print()
        print("Stopping...")

    finally:

        server.server_close()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()