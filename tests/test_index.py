"""Tests for 00-Index.md generator."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = str(
    Path(__file__).resolve().parent.parent
    / 'src' / 'akb2' / 'index' / 'generate_index.py'
)


def _make_page(wiki_dir: Path, name: str, frontmatter: str) -> None:
    """Helper: write a .md file with given frontmatter into wiki_dir."""
    (wiki_dir / name).write_text(
        f'---\n{frontmatter}\n---\nBody text.\n',
        encoding='utf-8',
    )


def test_empty_wiki_no_crash(tmp_path: Path) -> None:
    """Empty wiki/ dir with --dry-run should exit 0, output only header."""
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    result = subprocess.run(
        [sys.executable, SCRIPT, '--vault-root', str(tmp_path), '--dry-run'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert '# 00-Index' in result.stdout
    # No group headers for empty wiki
    assert '## ' not in result.stdout


def test_single_page_output_contains_title(tmp_path: Path) -> None:
    """A single active page should appear in the output with its title."""
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    _make_page(wiki, 'my-page.md', (
        'title: Test Page\n'
        'tldr: A short summary\n'
        'tags: [ai, agent]\n'
        'template_type: concept\n'
        'page_status: active'
    ))
    result = subprocess.run(
        [sys.executable, SCRIPT, '--vault-root', str(tmp_path), '--dry-run'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert 'Test Page' in result.stdout
    assert 'A short summary' in result.stdout


def test_real_vault_form_status_and_body_tldr(tmp_path: Path) -> None:
    """Real-vault form: status: active + body ## TLDR + no frontmatter tldr/tags.

    Mirrors AKB2.0 vault current shape; index must pick up the page and
    extract TLDR from body section, not crash on missing tags.
    """
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    (wiki / 'real-form-page.md').write_text(
        '---\n'
        'title: Real Form Page\n'
        'akb_layer: wiki\n'
        'doc_type: concept\n'
        'status: active\n'
        'canonical: true\n'
        'updated_at: 2026-05-17\n'
        '---\n\n'
        '# Real Form Page\n\n'
        '## TLDR\n\n'
        'Body TLDR paragraph captured by fallback.\n\n'
        '## Detail\n\n'
        'More content.\n',
        encoding='utf-8',
    )
    result = subprocess.run(
        [sys.executable, SCRIPT, '--vault-root', str(tmp_path), '--dry-run'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'Real Form Page' in result.stdout
    assert 'Body TLDR paragraph captured by fallback.' in result.stdout
    # tags fallback: must NOT crash; output should contain empty tags field
    assert 'tags:' in result.stdout


def test_index_preserves_external_content(tmp_path: Path) -> None:
    """SC-5: --apply preserves frontmatter + manual sections outside sentinels.

    Pre-existing 00-Index.md has YAML frontmatter, sub-directory links,
    and a manual maintenance section. After --apply, those must remain
    intact; only the AKB2-AUTO-GENERATED block is updated.
    """
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    # One active page so the generator has content to emit
    _make_page(wiki, 'my-page.md', (
        'title: Test Page\n'
        'tldr: A short summary\n'
        'tags: [ai]\n'
        'template_type: concept\n'
        'page_status: active'
    ))

    # Pre-existing index with rich external structure + an OLD generated block
    pre_existing_index = (
        '---\n'
        'maintained_by: praise\n'
        'last_review: 2026-04-01\n'
        '---\n\n'
        '# Knowledge Base Index\n\n'
        '## Sub-directories\n\n'
        '- [synthesis/](./synthesis/) — query results and synthesized notes\n'
        '- [concept/](./concept/) — concept pages\n\n'
        '## Maintenance log\n\n'
        '- 2026-04-01: praise reorganized synthesis/\n'
        '- 2026-04-15: bot rebuilt index\n\n'
        '<!-- AKB2-AUTO-GENERATED:start -->\n'
        '# 00-Index (stale auto-generated content to be replaced)\n'
        '<!-- AKB2-AUTO-GENERATED:end -->\n\n'
        '## Manual notes below\n\n'
        '- Never auto-delete this section.\n'
    )
    (wiki / '00-Index.md').write_text(pre_existing_index, encoding='utf-8')

    result = subprocess.run(
        [sys.executable, SCRIPT, '--vault-root', str(tmp_path), '--apply'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    after = (wiki / '00-Index.md').read_text(encoding='utf-8')

    # External structure preserved
    assert 'maintained_by: praise' in after, 'frontmatter lost'
    assert '# Knowledge Base Index' in after, 'top-level heading lost'
    assert '## Sub-directories' in after, 'sub-dir section lost'
    assert '[synthesis/](./synthesis/)' in after, 'sub-dir link lost'
    assert '## Maintenance log' in after, 'maintenance log lost'
    assert '2026-04-15: bot rebuilt index' in after, 'maintenance entry lost'
    assert '## Manual notes below' in after, 'trailing manual section lost'
    assert 'Never auto-delete this section.' in after, 'manual note lost'

    # Auto-generated region updated (new content, stale heading replaced)
    assert 'stale auto-generated content' not in after, (
        'stale auto-generated block must be replaced'
    )
    assert 'Test Page' in after, 'new generated content missing'

    # Sentinels still present (re-anchorable for next run)
    assert '<!-- AKB2-AUTO-GENERATED:start -->' in after
    assert '<!-- AKB2-AUTO-GENERATED:end -->' in after


def test_multiple_template_types_grouping(tmp_path: Path) -> None:
    """Pages with different template_types produce separate ## sections."""
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    _make_page(wiki, 'page-a.md', (
        'title: Page A\n'
        'tldr: First\n'
        'tags: [x]\n'
        'template_type: concept\n'
        'page_status: active'
    ))
    _make_page(wiki, 'page-b.md', (
        'title: Page B\n'
        'tldr: Second\n'
        'tags: [y]\n'
        'template_type: tool\n'
        'page_status: live'
    ))
    result = subprocess.run(
        [sys.executable, SCRIPT, '--vault-root', str(tmp_path), '--dry-run'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert '## concept' in result.stdout
    assert '## tool' in result.stdout
    assert 'Page A' in result.stdout
    assert 'Page B' in result.stdout
