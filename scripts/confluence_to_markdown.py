#!/usr/bin/env python3
"""Convert Confluence HTML+ (content_format="html" from getConfluenceContent) to markdown.

Standard elements (headings, bold/italic/strike, tables, lists, hr, fenced
code with language, blockquotes) are delegated to the `html-to-markdown`
package. Confluence-specific node types that library doesn't understand are
handled explicitly so nothing is silently dropped:

- table of contents macro  -> single-line HTML comment
- images (media)            -> single-line HTML comment (alt text + media id)
- expand sections           -> inner content converted normally, wrapped in
                                start/end HTML comments
- layout sections           -> each column converted normally, preceded by a
                                column marker carrying its width, wrapped in
                                start/end HTML comments carrying the layout
                                type
- panels (note/info/warning/success/error) -> inner content converted
                                normally, wrapped in a GitHub-style
                                `> [!TYPE]` admonition blockquote
- complex tables (merged cells, or block content in a cell) -> kept as raw
                                HTML, since a markdown table can't hold them
- inline elements markdown can't express (underline, dates, and spans with
  a data-type or style: mentions, status, emoji, color) -> kept as raw HTML

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


def _process_expand(soup: BeautifulSoup, details, replacements: dict) -> None:
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


def _process_layout(soup: BeautifulSoup, section, replacements: dict) -> None:
    columns = section.find_all("div", attrs={"data-type": "column"}, recursive=False)
    col_mds = []
    for col in columns:
        col_html = "".join(str(c) for c in col.contents)
        col_mds.append(
            f'<!-- Confluence layout column: {col.get("data-width", "")} -->\n'
            + convert_fragment(col_html)
        )
    token = _make_token(replacements)
    replacements[token] = (
        f'<!-- Confluence layout section start: {section.get("data-type")} -->\n'
        + "\n".join(col_mds)
        + "\n<!-- Confluence layout section end -->"
    )
    _replace_node_with_token(soup, section, token)


_PANEL_TYPE_RE = re.compile(r"^panel-(\w+)$")


def _process_panel(soup: BeautifulSoup, panel, replacements: dict) -> None:
    match = _PANEL_TYPE_RE.match(panel.get("data-type", ""))
    panel_type = match.group(1).upper() if match else "NOTE"
    inner_html = "".join(str(c) for c in panel.contents)
    inner_md = convert_fragment(inner_html)
    lines = inner_md.splitlines() or [""]
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    token = _make_token(replacements)
    replacements[token] = f"> [!{panel_type}]\n{quoted}"
    _replace_node_with_token(soup, panel, token)


_CONTAINERS = (
    (lambda n: n.name == "details", _process_expand),
    (lambda n: n.name == "section" and re.match(r"^layout-", n.get("data-type", "")), _process_layout),
    (lambda n: n.name == "div" and _PANEL_TYPE_RE.match(n.get("data-type", "")), _process_panel),
)


def _process_containers(soup: BeautifulSoup, replacements: dict) -> None:
    """Expand sections, layouts and panels can nest in each other. Process the
    innermost first, so an outer container converts a token rather than a
    nested container it would flatten."""
    found = []
    for node in soup.find_all(True):
        for matches, process in _CONTAINERS:
            if matches(node):
                found.append((len(list(node.parents)), node, process))
                break
    for _, node, process in sorted(found, key=lambda f: -f[0]):
        process(soup, node, replacements)


def _is_complex_table(table) -> bool:
    """A table a markdown table can't hold: a merged cell, or a cell whose
    content is anything but at most one paragraph."""
    for cell in table.find_all(["td", "th"]):
        if cell.get("colspan", "1") != "1" or cell.get("rowspan", "1") != "1":
            return True
        blocks = [c for c in cell.children if getattr(c, "name", None) in _BLOCK_TAGS]
        if len(blocks) > 1 or any(b.name != "p" for b in blocks):
            return True
    return False


_BLOCK_TAGS = {
    "p", "ul", "ol", "pre", "table", "div", "blockquote", "figure", "details",
    "section", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
}


def _raw_html(node) -> str:
    """The node's HTML without `data-local-id`: Confluence assigns ids on its
    side, and they only clutter the markdown."""
    copy = BeautifulSoup(str(node), "html.parser")
    for tag in copy.find_all(attrs={"data-local-id": True}):
        del tag["data-local-id"]
    return str(copy)


def _process_complex_tables(soup: BeautifulSoup, replacements: dict) -> None:
    for table in soup.find_all("table"):
        if table.find_parent("table") or not _is_complex_table(table):
            continue
        token = _make_token(replacements)
        replacements[token] = _raw_html(table)
        _replace_node_with_token(soup, table, token)


def _is_passthrough_inline(node) -> bool:
    if node.name in ("u", "time"):
        return True
    return node.name == "span" and (node.has_attr("data-type") or node.has_attr("style"))


def _process_inline_passthrough(soup: BeautifulSoup, replacements: dict) -> None:
    """Keep inline elements markdown can't express as raw HTML. The token is a
    bare text node, so it stays inside its paragraph, heading or cell."""
    replaced = set()
    for node in soup.find_all(_is_passthrough_inline):
        if node.find_parent(["pre", "code"]) or any(id(p) in replaced for p in node.parents):
            continue
        replaced.add(id(node))
        token = _make_token(replacements)
        replacements[token] = _raw_html(node)
        node.replace_with(token)


def convert(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    replacements: dict = {}

    # Complex tables go first so they keep their raw content intact; then
    # toc/images and inline elements, so they are tokenized before an
    # expand/layout/panel converts its inner content.
    _process_complex_tables(soup, replacements)
    _process_toc(soup, replacements)
    _process_images(soup, replacements)
    _process_inline_passthrough(soup, replacements)
    _process_containers(soup, replacements)

    md = htm.convert(str(soup)).content
    # Resolve in reverse: a container's replacement can hold tokens created
    # before it, which must be resolved after it.
    for token, resolved in reversed(list(replacements.items())):
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
