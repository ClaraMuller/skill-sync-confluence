---
name: sync-confluence
description: >
  Syncs content between a local markdown file and the specific Confluence
  page it's linked to, in whichever direction has the more recent change.
  Use when the user asks to "sync with Confluence", "fetch updates from
  Confluence", "pull the latest from Confluence", "apply changes [to
  Confluence]", or otherwise wants a local .md file and its Confluence
  counterpart brought back in line. Works for any linked file, RFCs
  included — but for the RFC multi-pass review/editing workflow itself, use
  `alg-rfc-edit` instead.
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
  linked to a Confluence page — including an RFC doc, as long as the intent
  is a plain sync rather than the multi-pass RFC review/editing workflow
  (`alg-rfc-edit`).
- **Do NOT use** for a one-off read of a Confluence page with no local file
  involved — call the Confluence MCP tools directly instead.

## Prerequisites

- The Confluence MCP server must be available (tools like `getConfluencePage`
  / `updateConfluencePage` present in the tool list). If it isn't, stop and
  tell the user the skill can't run without it.
- This skill never creates the md file or the Confluence page — the user is
  responsible for creating both. If either doesn't exist, stop and ask the
  user to create it rather than creating it yourself.
- The conversion scripts (`scripts/confluence_to_markdown.py` and
  `scripts/markdown_to_confluence.py`) require the
  [`html-to-markdown`](https://pypi.org/project/html-to-markdown/) package
  (plus `beautifulsoup4` and `markdown`, used for the Confluence-specific
  pre/post-processing). Check with:
  ```
  pip list | grep html-to-markdown
  ```
  If that comes back empty, stop and tell the user to run
  `pip install html-to-markdown beautifulsoup4 markdown` before continuing —
  don't try to guess a workaround.

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
2. Fetch the page with `getConfluencePage` and read its last-modified
   timestamp from the response (the version/history metadata, e.g.
   `version.when` — inspect the actual response shape, field names can vary
   by server version).
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

Fetch the page with `contentFormat: "html"` (not the default format —
the default flattens expand sections, layouts, and images into plain text
with no structure, which loses exactly the elements this skill needs to
preserve). Run that HTML through `scripts/confluence_to_markdown.py` to get
the candidate markdown:

```
python3 scripts/confluence_to_markdown.py --input <page.html> --output <candidate.md>
```

Review the output for anything outside expand sections / images / layouts /
panels / the table-of-contents macro that came out looking wrong — those 5
are handled explicitly by the script (see Step 5's Pull bullet); anything else
falls back to the underlying `html-to-markdown` library's default rendering
and may need a manual touch-up before it's shown to the user.

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
  script for 5 element types — verify the result looks right rather than
  hand-writing these:

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
    <!-- Confluence layout section start -->
    ...converted inner content (one block per column)...
    <!-- Confluence layout section end -->
    ```

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

  Anything the script doesn't recognize (any Confluence element outside
  these 5 types) is left to `html-to-markdown`'s default rendering — flag it
  by hand if it looks lossy, per Step 3.

- **Push (local → Confluence):** run `scripts/markdown_to_confluence.py` on
  the local file to get the HTML body — this reads the same 5 markers back
  and reconstructs the original `<details>`/`<section>`/`<figure>`/TOC/panel
  nodes (using any local edits to their inner content) instead of losing
  them:
  ```
  python3 scripts/markdown_to_confluence.py --input <local_file> --output <body.html>
  ```
  Then call `updateConfluencePage` with `contentFormat: "html"`, that body,
  and the `version` number read in Step 2 (Confluence requires the version
  to increment by exactly one).

## Output

A short report: which direction was synced (and why — which side was more
recent, or that the user chose), the diff that was applied, and the result
(new Confluence page version on push; confirmation the local file now
matches the page on pull). Don't create a separate report file.

## Notes & guardrails

- Never run state-mutating git commands as part of a sync — editing the
  local file is a plain file edit, not a git operation. Let the user commit
  if they want to.
- Never create the md file or the Confluence page yourself — only sync
  between two things that already exist.
- If the Confluence MCP server isn't available, or the page URL doesn't
  resolve, stop and say so rather than guessing.
- This skill is low-effort by design (`metadata.model: low`): all its steps
  are mechanical (timestamp comparison, diffing, applying an edit or a page
  update) with no open-ended judgment calls, so it should run under a
  low-effort/low-cost model rather than a higher one.
- `references/confluence-export-example.html` and
  `references/confluence-export-example.md` are a worked example of every
  supported element (TOC, separators, headings, rich text, tables,
  syntax-highlighted code blocks, an expand section, lists, an image, a
  3-column layout, 3 panel types, and a blockquote) round-tripped through
  both scripts — use them to sanity check the scripts still behave after
  any change.
- Known limitation: underline (`<u>`) has no markdown equivalent and is
  silently dropped by the underlying `html-to-markdown` library on pull —
  this is accepted, since underline isn't one of the 5 explicitly handled
  element types.
