"""Generate 00-Index.md from wiki pages with active/live status.

Hardening SC-5: --apply preserves external content via sentinel markers
  `<!-- AKB2-AUTO-GENERATED:start -->` … `<!-- AKB2-AUTO-GENERATED:end -->`

  - If target file has both sentinels: replace only content between them
    (preserves frontmatter, sub-directory links, manual sections)
  - If target file lacks sentinels: append sentineled block to end
    (preserves any pre-existing content)
  - If target file does not exist: write fresh with sentineled block
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


SENTINEL_START = "<!-- AKB2-AUTO-GENERATED:start -->"
SENTINEL_END = "<!-- AKB2-AUTO-GENERATED:end -->"
_SENTINEL_BLOCK_RE = re.compile(
    re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
    re.DOTALL,
)


def parse_frontmatter(text: str) -> dict:
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
    m = re.search(r'^##\s+TLDR\s*$', text, re.MULTILINE)
    if not m:
        return ''
    after = text[m.end():]
    end_m = re.search(r'^##\s+\S', after, re.MULTILINE)
    section = after[: end_m.start()] if end_m else after
    paras = [p.strip() for p in re.split(r'\n\s*\n', section) if p.strip()]
    if not paras:
        return ''
    return re.sub(r'\s+', ' ', paras[0])


def collect_pages(wiki_dir: Path) -> list[dict]:
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


def render_generated_block(pages: list[dict]) -> str:
    """Render auto-generated content wrapped in sentinels."""
    lines = [SENTINEL_START, '# 00-Index', '']
    if pages:
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
    lines.append(SENTINEL_END)
    return '\n'.join(lines)


def merge_with_existing(existing_text: str, generated_block: str) -> str:
    """SC-5: replace sentineled region only; preserve everything else."""
    if _SENTINEL_BLOCK_RE.search(existing_text):
        return _SENTINEL_BLOCK_RE.sub(generated_block, existing_text, count=1)
    sep = '' if existing_text.endswith('\n') else '\n'
    return existing_text + sep + '\n' + generated_block + '\n'


# Back-compat alias used by existing tests
def render_index(pages: list[dict]) -> str:
    """Render the full index file (dry-run output / fresh-file form)."""
    return render_generated_block(pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Generate 00-Index.md from wiki pages.',
    )
    parser.add_argument('--vault-root', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', default=True)
    mode.add_argument('--apply', action='store_true', default=False)
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root)
    wiki_dir = vault_root / 'wiki'

    if not wiki_dir.is_dir():
        print(f'Error: wiki directory not found: {wiki_dir}', file=sys.stderr)
        return 1

    pages = collect_pages(wiki_dir)
    generated_block = render_generated_block(pages)

    if args.apply:
        try:
            out_path = wiki_dir / '00-Index.md'
            if out_path.exists():
                existing = out_path.read_text(encoding='utf-8')
                merged = merge_with_existing(existing, generated_block)
                out_path.write_text(merged, encoding='utf-8')
            else:
                out_path.write_text(generated_block + '\n', encoding='utf-8')
        except OSError as e:
            print(f'Error writing index: {e}', file=sys.stderr)
            return 1
    else:
        print(generated_block, end='')

    return 0


if __name__ == '__main__':
    sys.exit(main())
