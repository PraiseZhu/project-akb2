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
