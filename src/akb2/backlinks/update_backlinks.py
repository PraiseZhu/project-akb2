#!/usr/bin/env python3
"""
AKB2 backlinks updater.
Maintains data/backlinks.jsonl:
  each line: {"source_slug": "...", "referenced_by": ["qr-slug-1", ...]}
"""
import argparse
import json
import sys
from pathlib import Path


def parse_frontmatter(text):
    """Extract YAML frontmatter as dict (simple key: value only, no nesting except lists)."""
    if not text.startswith('---'):
        return {}
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}
    fm_text = parts[1]
    result = {}
    # Parse simple key: value and key: [list] patterns
    current_key = None
    current_list = None
    for line in fm_text.splitlines():
        if ':' in line and not line.startswith(' ') and not line.startswith('-'):
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip()
            if val == '' or val == '[]':
                current_key = key
                current_list = []
                result[key] = current_list
            elif val.startswith('[') and val.endswith(']'):
                items = [s.strip().strip('"\'') for s in val[1:-1].split(',') if s.strip()]
                result[key] = items
                current_key = None
                current_list = None
            else:
                result[key] = val.strip('"\'')
                current_key = None
                current_list = None
        elif line.strip().startswith('-') and current_list is not None:
            item = line.strip().lstrip('-').strip().strip('"\'')
            if item:
                current_list.append(item)
    return result


def load_backlinks(backlinks_file: Path) -> dict:
    """Load existing backlinks.jsonl into a dict keyed by source_slug."""
    backlinks = {}
    if not backlinks_file.exists():
        return backlinks
    with backlinks_file.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                source_slug = entry.get('source_slug', '')
                if source_slug:
                    backlinks[source_slug] = entry.get('referenced_by', [])
            except json.JSONDecodeError:
                # Skip malformed lines
                continue
    return backlinks


def save_backlinks(backlinks: dict, backlinks_file: Path) -> None:
    """Write backlinks dict to backlinks.jsonl."""
    backlinks_file.parent.mkdir(parents=True, exist_ok=True)
    with backlinks_file.open('w', encoding='utf-8') as f:
        for source_slug, referenced_by in sorted(backlinks.items()):
            entry = {
                'source_slug': source_slug,
                'referenced_by': sorted(set(referenced_by)),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def get_slug_from_file(filepath: Path, frontmatter: dict) -> str:
    """Derive slug from frontmatter 'slug' field, or fall back to filename stem."""
    return frontmatter.get('slug', '') or filepath.stem


def rebuild(wiki_root: Path, backlinks_file: Path) -> int:
    """Full rebuild: scan all synthesis/*.md, extract derived_from, write backlinks.jsonl."""
    synthesis_dir = wiki_root / 'synthesis'
    if not synthesis_dir.is_dir():
        print(f"[ERROR] synthesis dir not found: {synthesis_dir}", file=sys.stderr)
        return 1

    md_files = list(synthesis_dir.glob('*.md'))
    if not md_files:
        # 空 synthesis 是合法状态(还没 promote 任何页面)：写空 backlinks，正常退出。
        print(f"[WARN] No .md files found in {synthesis_dir} — writing empty backlinks", file=sys.stderr)
        save_backlinks({}, backlinks_file)
        return 0

    backlinks: dict = {}

    for md_file in md_files:
        try:
            text = md_file.read_text(encoding='utf-8')
        except OSError as e:
            print(f"[WARN] Cannot read {md_file}: {e}", file=sys.stderr)
            continue

        try:
            fm = parse_frontmatter(text)
        except Exception as e:
            print(f"[WARN] Frontmatter parse failed for {md_file}: {e}", file=sys.stderr)
            continue

        qr_slug = get_slug_from_file(md_file, fm)
        derived_from = fm.get('derived_from', [])

        # Normalize: derived_from may be a string or list
        if isinstance(derived_from, str):
            derived_from = [derived_from] if derived_from else []

        for source_slug in derived_from:
            source_slug = source_slug.strip()
            if not source_slug:
                continue
            if source_slug not in backlinks:
                backlinks[source_slug] = []
            if qr_slug not in backlinks[source_slug]:
                backlinks[source_slug].append(qr_slug)

    save_backlinks(backlinks, backlinks_file)
    print(f"Rebuilt backlinks: {len(backlinks)} source entries from {len(md_files)} files")
    return 0


def add_entry(slug: str, wiki_root: Path, backlinks_file: Path) -> int:
    """Incremental update: process only the given slug's query-result page."""
    synthesis_dir = wiki_root / 'synthesis'
    if not synthesis_dir.is_dir():
        print(f"[ERROR] synthesis dir not found: {synthesis_dir}", file=sys.stderr)
        return 1

    md_file = synthesis_dir / f'{slug}.md'
    if not md_file.exists():
        print(f"[WARN] File not found: {md_file}", file=sys.stderr)
        return 0

    try:
        text = md_file.read_text(encoding='utf-8')
    except OSError as e:
        print(f"[ERROR] Cannot read {md_file}: {e}", file=sys.stderr)
        return 1

    try:
        fm = parse_frontmatter(text)
    except Exception as e:
        print(f"[WARN] Frontmatter parse failed for {md_file}: {e}", file=sys.stderr)
        return 0

    derived_from = fm.get('derived_from', [])
    if isinstance(derived_from, str):
        derived_from = [derived_from] if derived_from else []

    if not derived_from:
        print(f"[INFO] No derived_from found in {md_file}")
        return 0

    # Load existing backlinks
    backlinks = load_backlinks(backlinks_file)

    # Update: add slug to referenced_by for each source
    for source_slug in derived_from:
        source_slug = source_slug.strip()
        if not source_slug:
            continue
        if source_slug not in backlinks:
            backlinks[source_slug] = []
        if slug not in backlinks[source_slug]:
            backlinks[source_slug].append(slug)

    save_backlinks(backlinks, backlinks_file)
    print(f"Updated backlinks for {slug}: references {len(derived_from)} source(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='AKB2 backlinks updater — maintains <root>/data/backlinks.jsonl',
    )
    # root 二选一：--vault-root 对齐生态(generate_index.py)语义=AKB2.0 根，内部推 wiki/synthesis；
    # --wiki-root 为兼容旧调用(直接指向含 synthesis/ 的 wiki 目录)。
    root_group = parser.add_mutually_exclusive_group(required=True)
    root_group.add_argument(
        '--vault-root',
        help='AKB2.0 root directory (contains wiki/synthesis/*.md). Output defaults to <vault-root>/data/backlinks.jsonl',
    )
    root_group.add_argument(
        '--wiki-root',
        help='Legacy: path to wiki/ directory directly containing synthesis/*.md',
    )
    parser.add_argument(
        '--backlinks-file',
        default=None,
        help='Path to backlinks.jsonl output (default: <root>/data/backlinks.jsonl)',
    )
    parser.add_argument(
        '--rebuild',
        action='store_true',
        help='Full rebuild: scan all synthesis/*.md and rewrite backlinks.jsonl from scratch',
    )
    parser.add_argument(
        '--add-entry',
        metavar='SLUG',
        dest='add_entry_slug',
        help='Incremental update: process only the specified query-result slug',
    )

    args = parser.parse_args()

    # 路径推导：vault-root → wiki=vault/wiki，输出锚定 vault/data；
    #          wiki-root → 直接用，输出锚定 wiki 的父目录(即 AKB2.0 根)/data。
    if args.vault_root:
        vault_root = Path(args.vault_root)
        wiki_root = vault_root / 'wiki'
        default_base = vault_root
    else:
        wiki_root = Path(args.wiki_root)
        default_base = wiki_root.parent

    backlinks_file = (
        Path(args.backlinks_file)
        if args.backlinks_file
        else default_base / 'data' / 'backlinks.jsonl'
    )

    # Fail visibly: 错误的 root 必须非 0 退出，不静默(rule-12)。
    if not wiki_root.is_dir():
        print(f"[ERROR] wiki directory not found: {wiki_root}", file=sys.stderr)
        return 1

    if args.rebuild:
        return rebuild(wiki_root, backlinks_file)
    elif args.add_entry_slug:
        return add_entry(args.add_entry_slug, wiki_root, backlinks_file)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())
