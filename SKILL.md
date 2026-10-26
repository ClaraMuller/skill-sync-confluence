---
name: sync-confluence
description: >
  Syncs content between a local markdown file and the specific Confluence
  page it's linked to, in whichever direction has the more recent change.
  Use when the user asks to "sync with Confluence", "fetch updates from
  Confluence", "pull the latest from Confluence", "apply changes [to
  Confluence]", or otherwise wants a local .md file and its Confluence
  counterpart brought back in line. Works for any linked file.
metadata:
  author: clara.muller
  model: low
---

# Sync Confluence

Keeps one local markdown file and one specific Confluence page aligned, by
syncing from whichever side changed more recently.

## When to use

- Trigger: "sync with Confluence", "fetch updates from Confluence", "pull
  the latest from Confluence", "apply changes [to Confluence]".
- Trigger: the user references a local `.md` file that is (or should be)
  linked to a Confluence page, and wants the two brought back in line.
- **Do NOT use** for a one-off read of a Confluence page with no local file
  involved — call the Atlassian MCP v2 Confluence tools directly instead.

## Prerequisites

- The Atlassian MCP v2 server must be available (tools
  `mcp__claude_ai_Atlassian_MCP__getConfluenceContent` /
  `updateConfluenceContent` present in the tool list; the legacy
  `Confluence_MCP` connector no longer works). If it isn't, stop and tell the
  user the skill can't run without it.
- Every v2 call needs a top-level `cloudId`. Call
  `getAccessibleAtlassianResources` once per session and reuse the result.
- This skill never creates the md file or the Confluence page — the user is
  responsible for creating both. If either doesn't exist, stop and ask the
  user to create it rather than creating it yourself.
- The conversion scripts (`scripts/confluence_to_markdown.py` and
  `scripts/markdown_to_confluence.py`) require the
  [`html-to-markdown`](https://pypi.org/project/html-to-markdown/) package
  (plus `beautifulsoup4` and `markdown`, used for the Confluence-specific
  pre/post-processing), at the versions pinned in `requirements.txt`. Check
  with:
  ```
  pip list | grep -i -E "html-to-markdown|beautifulsoup4|markdown"
  ```
  If a package is missing or its version differs from `requirements.txt`,
  stop and tell the user to run
  `pip install -r <skill_dir>/requirements.txt` before continuing — don't
  try to guess a workaround.

## Inputs

- `local_file` — path to the local markdown file. If not given, ask.

## Step 1 — Find the linked Confluence page

Read the local file and look for a hidden comment anywhere in it of the
form:

```
<!-- Confluence sync link: <URL> -->
```

- **If found:** use that URL as `confluence_page`.
- **If not found:** ask the user for the Confluence page URL
  (`AskUserQuestion` or a direct question). Once given, insert the comment
  into the file (e.g. as the first line) so future syncs auto-detect it —
  don't ask again next time.

## Step 2 — Determine which side changed more recently

1. Get the local file's last-modified time:
   ```
   stat -f '%m' <local_file>      # macOS: epoch seconds
   ```
2. Fetch the page with `getConfluenceContent` (`content_url` = the sync
   link, `content_format: "html"`, `detail: "full"`) and read its
   last-modified timestamp from `metadata.version.createdAt`. Keep the
   top-level `snapshotToken` (e.g. `"v:12"`) from the same response — the push
   needs it.
3. Compare the two timestamps:
   - **Clearly later on Confluence** → direction is **pull** (Confluence →
     local).
   - **Clearly later on local file** → direction is **push** (local →
     Confluence).
   - **Within ~60 seconds of each other** (clock skew, or right after a
     previous sync) → there's no clear recommendation; Step 4's
     confirmation becomes a plain either/or choice with no default
     highlighted.

## Step 3 — Show the direct diff before applying anything

Use the page HTML fetched in Step 2 (`content_format: "html"` with
`detail: "full"` — the default `detail` is a summary with no body, and a
non-HTML format loses exactly the elements this skill needs to preserve).
Save `body.value` to a file and run that HTML through `scripts/confluence_to_markdown.py` to get
the candidate markdown:

```
python3 scripts/confluence_to_markdown.py --input <page.html> --output <candidate.md>
```

Review the output for anything outside the elements the script handles
explicitly (see Step 5's Pull bullet) that came out looking wrong;
anything else falls back to the underlying `html-to-markdown` library's
default rendering and may need a manual touch-up before it's shown to the
user.

Then write both versions (the current local file, and the candidate
markdown) to two temp files, and run a real colored diff and show its
output verbatim — never describe the diff in prose sentences:

```
diff --color=always -u <old_file> <new_file>
```

("old" = whichever side is being replaced, "new" = whichever side wins per
Step 2). If no changes are detected, say so plainly instead of running the
diff.

## Step 4 — Confirm the direction

Regardless of how confident Step 2's timestamp comparison was, always
confirm the direction with the user before writing anything — use
`AskUserQuestion` with the direction from Step 2 pre-filled as the
recommended option, e.g.: "Confluence was updated more recently — pull
Confluence → local (recommended), or push local → Confluence instead?".
Never apply a sync the user hasn't explicitly confirmed in this step, even
when Step 2 pointed to an obvious direction.

## Step 5 — Apply the sync

- **Pull (Confluence → local):** update the local file with `Edit`/`Write`
  to match `scripts/confluence_to_markdown.py`'s output (already produced in
  Step 3). Keep the `<!-- Confluence sync link: ... -->` comment and any
  other local-only front-matter unless the user says to drop them.

  **Non-representable Confluence content** is handled automatically by the
  script for the element types below — verify the result looks right rather
  than hand-writing these:

  - **Table of contents macro** and **pictures/images** have no
    markdown-representable content of their own, so each becomes a single
    HTML comment:
    `<!-- Confluence content not shown here: table of contents macro — see Confluence page -->`
    `<!-- Confluence content not shown here: image "<alt text>" (media-id: <id>) — see Confluence page -->`

  - **Expand sections** and **layouts** (multi-column layout sections) *do*
    contain nested content that markdown can otherwise represent (text,
    lists, tables, …), so their inner content is converted normally but
    bracketed so it's never mistaken for ordinary local markdown content:

    ```
    <!-- Confluence expand section start: "<title>" -->
    ...converted inner content...
    <!-- Confluence expand section end -->
    ```

    ```
    <!-- Confluence layout section start: <layout type, e.g. layout-two-equal> -->
    <!-- Confluence layout column: <width, e.g. 50> -->
    ...converted content of column 1...
    <!-- Confluence layout column: <width> -->
    ...converted content of column 2...
    <!-- Confluence layout section end -->
    ```

    Each column starts with its own marker, so a column can hold several
    paragraphs. Expand sections, layouts and panels can nest in each other.

  - **Complex tables** (a merged cell, or a list, code block or several
    paragraphs in a cell) can't be a markdown table, so they stay as the
    original Confluence HTML (without `data-local-id`). Edit their text as
    HTML.

  - **Inline elements markdown can't express** stay as raw inline HTML
    inside the paragraph: underline (`<u>`), dates (`<time datetime>`), and
    any `<span>` with a `data-type` (mentions, status, emoji) or a `style`
    (text color). For example `<u>underlined</u>` or
    `<time datetime="2026-09-01">September 1, 2026</time>`.

  - **Panels** (note/info/warning/success/error) also contain
    markdown-representable inner content, so they're converted the same way
    but as a GitHub-style admonition blockquote — the literal Confluence
    panel type, uppercased, inside `[! ]` (not limited to GitHub's own 5
    keywords, so the exact type always survives a round trip):

    ```
    > [!NOTE]
    > ...converted inner content, one blockquote line per line...
    ```

    A plain `> quoted text` with no `[!TYPE]` marker is an ordinary
    blockquote (Confluence's `<blockquote>`, which already converts cleanly
    both ways) — not a panel, and needs no special handling.

  - **Task lists** round-trip as GitHub-style checkbox items, one per line:

    ```
    - [ ] Not done
    - [x] Done
    ```

    On push, `scripts/markdown_to_confluence.py` reconstructs
    `<ul data-type="task-list"><li data-type="task-item"><input
    type="checkbox" checked> ...</li></ul>` (the `checked` attribute only on
    `[x]` items) — the underlying `markdown` package has no built-in
    checklist support and would otherwise leave the literal `[ ] ` text in a
    plain bullet.

  Anything the script doesn't recognize (any Confluence element outside
  these types) is left to `html-to-markdown`'s default rendering — flag it
  by hand if it looks lossy, per Step 3.

- **Push (local → Confluence):** run `scripts/markdown_to_confluence.py` on
  the local file to get the HTML body — this reads the same markers back
  and reconstructs the original `<details>`/`<section>`/`<figure>`/TOC/panel
  nodes (using any local edits to their inner content) instead of losing
  them:
  ```
  python3 scripts/markdown_to_confluence.py --input <local_file> --output <body.html>
  ```
  Then:
  1. Once per session, before the first write, call `getConfluenceSpace`
     (via `executeRead`, `inputs: {spaceIdOrKey}`) and apply any
     `spaceInstructions` it returns. Skip it when the Step 2 response has
     `metadata.hasSpaceInstructions: false`.
  2. Call `updateConfluenceContent` with `contentId`, `body: {format:
     "html", value: <body.html>}` and the `snapshotToken` from Step 2.
  3. If it fails with `snapshot_stale`, the page changed since Step 2: re-read
     it, redo Step 3's diff and Step 4's confirmation, then retry with the
     new token. Never resend the same payload with the stale token.

## Output

A short report: which direction was synced (and why — which side was more
recent, or that the user chose), the diff that was applied, and the result
(new `snapshotToken` / version number on push; confirmation the local file now
matches the page on pull). Don't create a separate report file.

## Notes & guardrails

- Never run state-mutating git commands as part of a sync — editing the
  local file is a plain file edit, not a git operation. Let the user commit
  if they want to.
- Never create the md file or the Confluence page yourself — only sync
  between two things that already exist.
- If the Atlassian MCP v2 server isn't available, or the page URL doesn't
  resolve, stop and say so rather than guessing.
- This skill is low-effort by design (`metadata.model: low`): all its steps
  are mechanical (timestamp comparison, diffing, applying an edit or a page
  update) with no open-ended judgment calls, so it should run under a
  low-effort/low-cost model rather than a higher one.
- `references/confluence-export-example.html` and
  `references/confluence-export-example.md` are a worked example of every
  supported element (TOC, separators, headings, rich text, a date, tables,
  syntax-highlighted code blocks, code block settings, an expand section,
  lists, an image, a 3-column layout, 3 panel types, a blockquote, and a
  task list)
  round-tripped through both scripts — use them to sanity check the scripts
  still behave after any change.

## Known limitations

What the conversion loses, verified with `html-to-markdown` 3.18.6 against
the reference page and a round trip (pull, then push). A push re-sends the
whole body, so anything lost on pull is also removed from the page on the
next push. Step 3's markdown diff does not show these losses.

| Confluence element             | Lost on pull (Confluence → markdown)                     | Result on the page after a push                       |
|--------------------------------|----------------------------------------------------------|-------------------------------------------------------|
| Unknown macros (e.g. Jira)     | Whole macro, silently (no placeholder)                   | Macro deleted                                         |
| Simple table layout            | `data-width`, column widths, `<p>` in cells              | Default widths                                        |
| Code block settings            | `data-wrap`, `data-hide-line-numbers`, breakout width    | Default settings (language is kept)                   |
| Table of contents macro        | Macro parameters (style, macro id)                       | Fresh TOC macro with defaults                         |
| Images                         | Caption, size, collection; kept as a placeholder comment | Caption replaced by the alt text, default size        |
| Expand and layout sections     | Breakout width                                           | Default width                                         |
| Nested lists                   | Nothing (kept as an indented sub-list)                   | Sub-list flattened into the parent list               |
| Every node                     | `data-local-id`                                          | New ids, so inline comments lose their anchors        |

Layouts (type, columns, widths), merged cells, lists in table cells,
mentions, status, emoji, text color, underline and dates round-trip
losslessly. The real Confluence markup for mentions, status and emoji
hasn't been checked against a live page yet.
