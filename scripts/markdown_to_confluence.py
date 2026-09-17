#!/usr/bin/env python3
"""Convert a local markdown file (produced by confluence_to_markdown.py, or
hand-edited from it) back into Confluence HTML+ suitable for
updateConfluencePage with contentFormat="html".

Reverses the 5 special-case markers that confluence_to_markdown.py inserts,
reconstructing the original Confluence node instead of losing it:

- table of contents comment -> fresh TOC extension div
- image comment              -> media figure, using the media id in the comment
- expand section markers     -> <details><summary>...</summary>...</details>,
                                 re-converting the (possibly edited) inner
                                 markdown back to HTML
- layout section markers     -> <section data-type="layout-N-equal"> with one
                                 <div data-type="column"> per column,
                                 re-converting each column's markdown
- `> [!TYPE]` admonition      -> <div data-type="panel-{type}">, re-converting
                                 the (possibly edited) inner markdown back to
                                 HTML

Everything else is converted with the `markdown` package (tables +
fenced_code extensions).

Usage:
    python3 markdown_to_confluence.py --input page.md --output page.html
    cat page.md | python3 markdown_to_confluence.py > page.html
"""

import argparse
import re
import sys
import uuid

import markdown as md_lib

TOC_RE = re.compile(
    r"<!-- Confluence content not shown here: table of contents macro"
    r" — see Confluence page -->\n?"
)
IMAGE_RE = re.compile(
    r'<!-- Confluence content not shown here: image "(?P<alt>.*?)" '
    r"\(media-id: (?P<media_id>.*?)\) — see Confluence page -->\n?"
)
EXPAND_RE = re.compile(
    r'<!-- Confluence expand section start: "(?P<title>.*?)" -->\n'
    r"(?P<inner>.*?)\n"
    r"<!-- Confluence expand section end -->\n?",
    re.DOTALL,
)
LAYOUT_RE = re.compile(
    r"<!-- Confluence layout section start -->\n"
    r"(?P<inner>.*?)\n"
    r"<!-- Confluence layout section end -->\n?",
    re.DOTALL,
)
PANEL_RE = re.compile(
    r"^> \[!(?P<type>[A-Z]+)\]\n(?P<body>(?:>.*(?:\n|$))*)",
    re.MULTILINE,
)


_LIST_BOUNDARY = "CONFLISTBOUNDARY" + "0" * 16
_STRIKETHROUGH_RE = re.compile(r"~~(.+?)~~")


def _insert_list_boundaries(text: str) -> str:
    """python-markdown doesn't start a new list when the marker style changes
    (numbered -> bulleted or vice versa) across a blank line — it just keeps
    appending to the same list. Force a break with a raw-HTML-comment blank
    block, which IS enough to split them."""
    marker = f"\n\n<!--{_LIST_BOUNDARY}-->\n\n"
    text = re.sub(r"(?m)(^\d+\.[ \t].*)\n\n(?=[-*+][ \t])", r"\1" + marker, text)
    text = re.sub(r"(?m)(^[-*+][ \t].*)\n\n(?=\d+\.[ \t])", r"\1" + marker, text)
    return text


def convert_fragment(markdown_text: str) -> str:
    """Convert a plain markdown fragment (no special markers expected) to HTML."""
    if not markdown_text.strip():
        return ""
    text = _STRIKETHROUGH_RE.sub(r"<s>\1</s>", markdown_text)
    text = _insert_list_boundaries(text)
    html = md_lib.markdown(text, extensions=["tables", "fenced_code"])
    html = html.replace(f"<!--{_LIST_BOUNDARY}-->\n\n", "").replace(
        f"<!--{_LIST_BOUNDARY}-->", ""
    )
    return html


def _token() -> str:
    return "CONFSENTINEL" + uuid.uuid4().hex


def _replace_toc(text: str, replacements: dict) -> str:
    def repl(_match: re.Match) -> str:
        token = _token()
        replacements[token] = (
            '<div data-type="extension" data-extension-key="toc" '
            'data-extension-type="com.atlassian.confluence.macro.core" '
            'data-layout="default"></div>'
        )
        return token

    return TOC_RE.sub(repl, text)


def _replace_images(text: str, replacements: dict) -> str:
    def repl(match: re.Match) -> str:
        alt = match.group("alt")
        media_id = match.group("media_id")
        token = _token()
        replacements[token] = (
            '<figure data-type="media-single" data-layout="center">'
            f'<div data-type="media" data-media-type="file" data-id="{media_id}" '
            f'data-alt="{alt}"></div>'
            f"<figcaption>{alt}</figcaption></figure>"
        )
        return token

    return IMAGE_RE.sub(repl, text)


def _replace_expand(text: str, replacements: dict) -> str:
    def repl(match: re.Match) -> str:
        title = match.group("title")
        inner_html = convert_fragment(match.group("inner"))
        token = _token()
        replacements[token] = f"<details><summary>{title}</summary>{inner_html}</details>"
        return token

    return EXPAND_RE.sub(repl, text)


def _replace_layout(text: str, replacements: dict) -> str:
    def repl(match: re.Match) -> str:
        inner = match.group("inner")
        columns = [c for c in inner.split("\n\n") if c.strip()] if inner.strip() else []
        if not columns:
            columns = [""]
        layout_type = {2: "layout-two-equal", 3: "layout-three-equal"}.get(
            len(columns), f"layout-{len(columns)}-equal"
        )
        width = round(100 / len(columns), 2)
        cols_html = "".join(
            f'<div data-type="column" data-width="{width}">{convert_fragment(col)}</div>'
            for col in columns
        )
        token = _token()
        replacements[token] = f'<section data-type="{layout_type}">{cols_html}</section>'
        return token

    return LAYOUT_RE.sub(repl, text)


def _replace_panels(text: str, replacements: dict) -> str:
    def repl(match: re.Match) -> str:
        panel_type = match.group("type").lower()
        lines = []
        for line in match.group("body").splitlines():
            if line.startswith("> "):
                lines.append(line[2:])
            elif line.startswith(">"):
                lines.append(line[1:])
            else:
                lines.append(line)
        inner_html = convert_fragment("\n".join(lines))
        token = _token()
        replacements[token] = f'<div data-type="panel-{panel_type}">{inner_html}</div>'
        return token

    return PANEL_RE.sub(repl, text)


def convert(markdown_text: str) -> str:
    replacements: dict = {}
    text = markdown_text
    text = _replace_toc(text, replacements)
    text = _replace_images(text, replacements)
    text = _replace_expand(text, replacements)
    text = _replace_layout(text, replacements)
    text = _replace_panels(text, replacements)

    # Guarantee each sentinel sits on its own line with blank lines on both
    # sides — without this, a preceding "---" line with no blank line above
    # a sentinel can get misread as a setext heading underline by the
    # `markdown` package, wrapping the reconstructed node in <h2>...</h2>.
    text = re.sub(r"\n?(CONFSENTINEL[0-9a-f]{32})\n?", r"\n\n\1\n\n", text)

    html = convert_fragment(text)
    for token, resolved in replacements.items():
        html = html.replace(token, resolved)
        # markdown wraps stray tokens in <p>...</p> when they sit on their
        # own line/paragraph; unwrap that around our reconstructed nodes.
        html = html.replace(f"<p>{resolved}</p>", resolved)
    return html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", help="Input markdown file (default: stdin)")
    parser.add_argument("--output", "-o", help="Output HTML file (default: stdout)")
    args = parser.parse_args()

    text = (
        open(args.input, encoding="utf-8").read()
        if args.input
        else sys.stdin.read()
    )
    html = convert(text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
    else:
        sys.stdout.write(html)


if __name__ == "__main__":
    main()
