"""update_backlinks.py 路径与退出码回归测试。

覆盖修复点：
  - --vault-root 语义对齐 generate_index(vault/wiki/synthesis)
  - 默认输出锚定 <root>/data/backlinks.jsonl(不再落 cwd)
  - 错误 root fail-visibly(非 0 退出)
  - --wiki-root 旧调用兼容
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "src" / "akb2" / "backlinks" / "update_backlinks.py"


def _make_synthesis(wiki: Path, slug: str, derived_from: list[str]) -> None:
    syn = wiki / "synthesis"
    syn.mkdir(parents=True, exist_ok=True)
    fm = f"---\nslug: {slug}\nderived_from: [{', '.join(derived_from)}]\n---\nbody\n"
    (syn / f"{slug}.md").write_text(fm, encoding="utf-8")


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def test_vault_root_finds_synthesis_under_wiki(tmp_path):
    """--vault-root 下 synthesis 在 wiki/ 子层，能正确扫到。"""
    vault = tmp_path / "AKB2.0"
    _make_synthesis(vault / "wiki", "qr-1", ["source-a", "source-b"])

    r = _run("--rebuild", "--vault-root", str(vault))
    assert r.returncode == 0, r.stderr

    out = vault / "data" / "backlinks.jsonl"
    assert out.exists(), f"默认输出应在 <vault>/data/，stderr={r.stderr}"
    entries = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    by_source = {e["source_slug"]: e["referenced_by"] for e in entries}
    assert by_source["source-a"] == ["qr-1"]
    assert by_source["source-b"] == ["qr-1"]


def test_default_output_anchored_not_cwd(tmp_path):
    """不传 --backlinks-file 时锚定 <vault>/data/，绝不落 cwd/data/。"""
    vault = tmp_path / "AKB2.0"
    _make_synthesis(vault / "wiki", "qr-x", ["src"])
    work = tmp_path / "elsewhere"
    work.mkdir()

    r = _run("--rebuild", "--vault-root", str(vault), cwd=work)
    assert r.returncode == 0, r.stderr
    assert (vault / "data" / "backlinks.jsonl").exists()
    assert not (work / "data" / "backlinks.jsonl").exists(), "不得写到 cwd"


def test_bad_vault_root_nonzero(tmp_path):
    """不存在的 root → 非 0 退出(fail visibly)。"""
    r = _run("--rebuild", "--vault-root", str(tmp_path / "nonexistent"))
    assert r.returncode != 0


def test_synthesis_dir_missing_nonzero(tmp_path):
    """wiki 存在但缺 synthesis 子目录 → 非 0。"""
    vault = tmp_path / "AKB2.0"
    (vault / "wiki").mkdir(parents=True)
    r = _run("--rebuild", "--vault-root", str(vault))
    assert r.returncode != 0


def test_empty_synthesis_is_valid(tmp_path):
    """synthesis 存在但空(还没 promote) → 合法 0，写空 backlinks。"""
    vault = tmp_path / "AKB2.0"
    (vault / "wiki" / "synthesis").mkdir(parents=True)
    r = _run("--rebuild", "--vault-root", str(vault))
    assert r.returncode == 0, r.stderr
    assert (vault / "data" / "backlinks.jsonl").exists()


def test_wiki_root_legacy_compat(tmp_path):
    """旧 --wiki-root 直接指向含 synthesis 的目录仍可用。"""
    wiki = tmp_path / "wiki"
    _make_synthesis(wiki, "qr-2", ["source-c"])
    bf = tmp_path / "out.jsonl"

    r = _run("--rebuild", "--wiki-root", str(wiki), "--backlinks-file", str(bf))
    assert r.returncode == 0, r.stderr
    entries = [json.loads(x) for x in bf.read_text().splitlines() if x.strip()]
    assert any(e["source_slug"] == "source-c" for e in entries)


def test_wiki_root_default_anchors_to_parent(tmp_path):
    """--wiki-root 模式默认输出锚定 wiki 父目录(AKB2.0 根)/data/。"""
    vault = tmp_path / "AKB2.0"
    _make_synthesis(vault / "wiki", "qr-3", ["src-d"])
    r = _run("--rebuild", "--wiki-root", str(vault / "wiki"), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (vault / "data" / "backlinks.jsonl").exists()


def test_requires_a_root(tmp_path):
    """既不给 --vault-root 也不给 --wiki-root → argparse 非 0。"""
    r = _run("--rebuild")
    assert r.returncode != 0


def test_mutually_exclusive_roots(tmp_path):
    """同时给两个 root → argparse 非 0。"""
    r = _run("--rebuild", "--vault-root", str(tmp_path), "--wiki-root", str(tmp_path))
    assert r.returncode != 0


def test_add_entry_vault_root(tmp_path):
    """增量 --add-entry 在 --vault-root 模式下正确更新。"""
    vault = tmp_path / "AKB2.0"
    _make_synthesis(vault / "wiki", "qr-inc", ["source-e"])
    r = _run("--add-entry", "qr-inc", "--vault-root", str(vault))
    assert r.returncode == 0, r.stderr
    out = vault / "data" / "backlinks.jsonl"
    entries = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    by_source = {e["source_slug"]: e["referenced_by"] for e in entries}
    assert by_source["source-e"] == ["qr-inc"]
