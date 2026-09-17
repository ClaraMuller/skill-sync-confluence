#!/usr/bin/env python3
"""Convert Confluence HTML+ (contentFormat="html" from getConfluencePage) to markdown.

Standard elements (headings, bold/italic/strike, tables, lists, hr, fenced
code with language, blockquotes) are delegated to the `html-to-markdown`
package. Five Confluence-specific node types that library doesn't understand
are handled explicitly so nothing is silently dropped:

- table of contents macro  -> single-line HTML comment
- images (media)            -> single-line HTML comment (alt text + media id)
- expand sections           -> inner content converted normally, wrapped in
                                start/end HTML comments
- layout sections           -> each column converted normally, wrapped in
                                start/end HTML comments
- panels (note/info/warning/success/error) -> inner content converted
                                normally, wrapped in a GitHub-style
                                `> [!TYPE]` admonition blockquote

Usage:
    python3 confluence_to_markdown.py --input page.html --output page.md
    cat page.html | python3 confluence_to_markdown.py > page.md
"""

import argparse
import re
import sys
import uuid

from bs4 import BeautifulSoup
import html_to_markdown as htm


def convert_fragment(html_fragment: str) -> str:
    """Convert a plain HTML fragment (no special nodes expected) to markdown."""
    if not html_fragment.strip():
        return ""
    return htm.convert(html_fragment).content.strip()


def _make_token(replacements: dict) -> str:
    token = "CONFSENTINEL" + uuid.uuid4().hex
    replacements[token] = None
    return token


def _replace_node_with_token(soup: BeautifulSoup, node, token: str) -> None:
    """Swap `node` for a <p> containing the sentinel `token`, rather than a
    bare text node — adjacent special elements (e.g. 3 panels in a row) with
    no text between them would otherwise have their sentinels concatenate
    with no separator, corrupting the final markdown."""
    p = soup.new_tag("p")
    p.string = token
    node.replace_with(p)


def _process_toc(soup: BeautifulSoup, replacements: dict) -> None:
    for node in soup.find_all(
        "div", attrs={"data-type": "extension", "data-extension-key": "toc"}
    ):
        token = _make_token(replacements)
        replacements[token] = (
            "<!-- Confluence content not shown here: table of contents macro "
            "— see Confluence page -->"
        )
        _replace_node_with_token(soup, node, token)


def _process_images(soup: BeautifulSoup, replacements: dict) -> None:
    for fig in soup.find_all("figure", attrs={"data-type": "media-single"}):
        media = fig.find("div", attrs={"data-type": "media"})
        caption = fig.find("figcaption")
        alt = (media.get("data-alt") if media else None) or (
            caption.get_text().strip() if caption else "image"
        )
        media_id = media.get("data-id", "") if media else ""
        token = _make_token(replacements)
        replacements[token] = (
            f'<!-- Confluence content not shown here: image "{alt}" '
            f"(media-id: {media_id}) — see Confluence page -->"
        )
        _replace_node_with_token(soup, fig, token)


def _process_expand(soup: BeautifulSoup, replacements: dict) -> None:
    for details in soup.find_all("details"):
        summary = details.find("summary")
        title = summary.get_text().strip() if summary else ""
        if summary:
            summary.extract()
        inner_html = "".join(str(c) for c in details.contents)
        inner_md = convert_fragment(inner_html)
        token = _make_token(replacements)
        replacements[token] = (
            f'<!-- Confluence expand section start: "{title}" -->\n'
            f"{inner_md}\n"
            f"<!-- Confluence expand section end -->"
        )
        _replace_node_with_token(soup, details, token)


def _process_layout(soup: BeautifulSoup, replacements: dict) -> None:
    for section in soup.find_all(
        "section", attrs={"data-type": re.compile(r"^layout-")}
    ):
        columns = section.find_all("div", attrs={"data-type": "column"}, recursive=False)
        col_mds = []
        for col in columns:
            col_html = "".join(str(c) for c in col.contents)
            col_mds.append(convert_fragment(col_html))
        token = _make_token(replacements)
        replacements[token] = (
            "<!-- Confluence layout section start -->\n"
            + "\n\n".join(col_mds)
            + "\n<!-- Confluence layout section end -->"
        )
        _replace_node_with_token(soup, section, token)


_PANEL_TYPE_RE = re.compile(r"^panel-(\w+)$")


def _process_panels(soup: BeautifulSoup, replacements: dict) -> None:
    for panel in soup.find_all("div", attrs={"data-type": _PANEL_TYPE_RE}):
        match = _PANEL_TYPE_RE.match(panel.get("data-type", ""))
        panel_type = match.group(1).upper() if match else "NOTE"
        inner_html = "".join(str(c) for c in panel.contents)
        inner_md = convert_fragment(inner_html)
        lines = inner_md.splitlines() or [""]
        quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
        token = _make_token(replacements)
        replacements[token] = f"> [!{panel_type}]\n{quoted}"
        _replace_node_with_token(soup, panel, token)


def convert(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    replacements: dict = {}

    # Process innermost-first is not required for this page's structure
    # (no nesting of these 5 types), but images/toc first avoids them being
    # accidentally swallowed by an expand/layout/panel inner-content
    # conversion.
    _process_toc(soup, replacements)
    _process_images(soup, replacements)
    _process_expand(soup, replacements)
    _process_layout(soup, replacements)
    _process_panels(soup, replacements)

    md = htm.convert(str(soup)).content
    for token, resolved in replacements.items():
        md = md.replace(token, resolved)
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", help="Input HTML file (default: stdin)")
    parser.add_argument("--output", "-o", help="Output markdown file (default: stdout)")
    args = parser.parse_args()

    html = (
        open(args.input, encoding="utf-8").read()
        if args.input
        else sys.stdin.read()
    )
    md = convert(html)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
