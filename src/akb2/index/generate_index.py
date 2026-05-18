"""Generate 00-Index.md from wiki pages with active/live status."""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter without PyYAML dependency."""
    m = re.match(r'^---\n(.*?)\n---\n', text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split('\n'):
        if ':' in line and not line.startswith(' '):
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [
                    x.strip().strip('"').strip("'")
                    for x in val[1:-1].split(',')
                    if x.strip()
                ]
            fm[key] = val
    return fm


def extract_body_tldr(text: str) -> str:
    """Extract the first non-empty paragraph after a `## TLDR` heading.

    Returns empty string if no `## TLDR` section exists. Stops at the next
    `## ` heading or end of file. Leading/trailing blank lines stripped;
    paragraph collapsed to single line for index display.
    """
    m = re.search(r'^##\s+TLDR\s*$', text, re.MULTILINE)
    if not m:
        return ''
    after = text[m.end():]
    # Stop at next ## heading
    end_m = re.search(r'^##\s+\S', after, re.MULTILINE)
    section = after[: end_m.start()] if end_m else after
    # First non-empty paragraph
    paras = [p.strip() for p in re.split(r'\n\s*\n', section) if p.strip()]
    if not paras:
        return ''
    # Collapse internal newlines to single spaces for one-line index entry
    return re.sub(r'\s+', ' ', paras[0])


def collect_pages(wiki_dir: Path) -> list[dict]:
    """Walk wiki/ recursively, parse frontmatter, return page metadata list.

    Status compat: prefer `page_status`, fall back to `status` (real-vault form).
    TLDR fallback: if frontmatter `tldr` missing, extract from body `## TLDR`.
    Tags: if frontmatter `tags` missing, leave empty (do not fabricate).
    """
    pages = []
    for root, _dirs, files in os.walk(wiki_dir):
        for fname in sorted(files):
            if not fname.endswith('.md'):
                continue
            if fname == '00-Index.md':
                continue
            fpath = Path(root) / fname
            text = fpath.read_text(encoding='utf-8')
            fm = parse_frontmatter(text)
            status = fm.get('page_status') or fm.get('status') or ''
            if status not in ('active', 'live'):
                continue
            tldr = fm.get('tldr') or extract_body_tldr(text)
            pages.append({
                'title': fm.get('title', fpath.stem),
                'tldr': tldr,
                'tags': fm.get('tags', []),
                'template_type': fm.get('template_type', 'uncategorized'),
            })
    return pages


def render_index(pages: list[dict]) -> str:
    """Render the 00-Index.md content from collected pages."""
    lines = ['<!-- AUTO-GENERATED -->', '# 00-Index', '']

    if not pages:
        return '\n'.join(lines) + '\n'

    groups: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        groups[p['template_type']].append(p)

    for ttype in sorted(groups):
        lines.append(f'## {ttype}')
        lines.append('')
        for p in sorted(groups[ttype], key=lambda x: x['title']):
            tags = p['tags']
            if isinstance(tags, list):
                tags_str = ', '.join(tags)
            else:
                tags_str = str(tags)
            lines.append(f"- **{p['title']}** — {p['tldr']} | tags: {tags_str}")
        lines.append('')

    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Generate 00-Index.md from wiki pages.',
    )
    parser.add_argument('--vault-root', required=True, help='Path to vault root')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--dry-run', action='store_true', default=True,
        help='Print to stdout (default)',
    )
    mode.add_argument(
        '--apply', action='store_true', default=False,
        help='Write to {vault-root}/wiki/00-Index.md',
    )
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root)
    wiki_dir = vault_root / 'wiki'

    if not wiki_dir.is_dir():
        print(f'Error: wiki directory not found: {wiki_dir}', file=sys.stderr)
        return 1

    pages = collect_pages(wiki_dir)
    content = render_index(pages)

    if args.apply:
        try:
            out_path = wiki_dir / '00-Index.md'
            out_path.write_text(content, encoding='utf-8')
        except OSError as e:
            print(f'Error writing index: {e}', file=sys.stderr)
            return 1
    else:
        print(content, end='')

    return 0


if __name__ == '__main__':
    sys.exit(main())
