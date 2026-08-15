"""sc-asm-cli 验收:组装落盘主链路 + (p) typed policy 三例 + (r) redaction 不可绕过。

集成断言用**真实** bootstrap 部署根 + 真实 archive_raw / anchors / rename
(非 monkeypatch 替身);monkeypatch 只用于计数与次序观测。

覆盖(packet sc-asm-cli expect + sc-asm-redact expect + p1a-sc0 ①–⑮):
  ① 集成:文件名 / 10 键 frontmatter / AEHMZ 锚点 / run_id 逐字
  (2a)(2a-2) 写盘所有权:archive_raw 恰一次且 output_dir 指向隔离目录;
     落位经原子改名恰一次;raw_dir 无中间产物
  (2a)(ii) clean/redact 两支逐字节比对(对照产物现场生成,同输入同环境)
  (2b) 隔离目录成功与失败两路径清理
  ③ run_id 贯通:CLI 缺失 exit 2 / 函数缺参 TypeError / ledger committed 逐字
  (p) 默认 Reject 零落位+隔离产物已销毁;CLI --redact 落盘+终检零命中+
     落盘==终检所扫;CLI 侧零写盘零替换
  (r) 三种凭证形态 exit 3 语义;scan 在 archive_raw 之后、落位之前;公开面无绕过
  (p1a) CLI→run_harvest_with_ledger 接线:identity 前置闸/闭包哨兵/台账
     终态/生产注入模式双模/reservation 透传/raw_abspath 同源/退出码全支
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from akb2.harvest import __main__ as cli  # noqa: E402
from akb2.harvest import assembler  # noqa: E402
from akb2.harvest import ledger as ledger_mod  # noqa: E402
from akb2.harvest import locks as locks_mod  # noqa: E402
from akb2.harvest import schema as schema_mod  # noqa: E402
from akb2.harvest.redaction import (  # noqa: E402
    REDACTED_MARKER,
    RedactionPolicy,
    RedactionRejectedError,
    scan_and_redact,
)
from src.akb2.quality.intake.models import compute_article_id  # noqa: E402

SECRET = "e3b0c44298fc1c149afbf4c8996fb924"
REQUIRED_ANCHOR_KEYS = ("A", "E", "H", "M", "Z")
FM_KEYS = {
    "artifact_type", "source_slug", "source_url", "source_title",
    "source_authors", "source_publisher", "source_published_at",
    "fetched_at", "fetched_via", "harvest_run_id",
}
RAW_NAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}--[0-9a-f]{12}--raw--\d{4}-\d{2}-\d{2}\.md$")


def _payload(session_id: str, with_secret: bool = False) -> dict:
    p = {
        "schema_version": "1",
        "session_id": session_id,
        "title": "harvest 集成测试",
        "lane_hint": "procedural",
        "components": ["gate"],
        "causal_edges": [],
        "atoms": [{"id": f"a{i}", "kind": "command", "text": f"text {i}"}
                  for i in range(1, 7)],
        "how_steps": [
            {"anchor": f"a{i}", "provenance": "log", "cmd": f"cmd{i}",
             "expect": "ok"} for i in range(1, 6)],
        "source_meta": {"tool": "claude-code", "author": "tester",
                        "publisher": "lab", "published_at": "2026-08-10"},
    }
    if with_secret:
        p["how_steps"][0]["cmd"] = (
            f"curl -H 'Authorization: Bearer {SECRET}'")
    return p


# ---------------------------------------------------------------------------
# 每测试唯一 ids(session_id 决定文件名,避免共享 raw_dir 下 EEXIST 串扰)
# ---------------------------------------------------------------------------


@pytest.fixture()
def ids(request: pytest.FixtureRequest) -> SimpleNamespace:
    digest = hashlib.sha256(request.node.name.encode()).hexdigest()
    return SimpleNamespace(
        run_id=f"{digest[0:8]}-{digest[8:12]}-4{digest[12:15]}-"
               f"a{digest[15:18]}-{digest[18:30]}",
        session_id=f"{digest[30:38]}-{digest[38:42]}-4{digest[42:45]}-"
                   f"8{digest[45:48]}-{digest[48:60]}",
    )


def _url(session_id: str) -> str:
    return f"cindy://session/{session_id}"


# ---------------------------------------------------------------------------
# session 级部署根:bootstrap 真实生成 anchor manifest(受信部署流程)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def deployment(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    tmp = tmp_path_factory.mktemp("harvest-deploy")
    uid, gid = os.getuid(), os.getgid()
    deploy_root = tmp / "deploy"
    deploy_root.mkdir(0o750)
    tree = tmp / "tree"
    tree.mkdir(0o750)
    for p in ("raw", "stage", "locks", "batch"):
        (tree / p).mkdir(0o750)
    (deploy_root / "roots.json").write_text(json.dumps({
        "raw_dir": str(tree / "raw"), "stage": str(tree / "stage"),
        "locks_dir": str(tree / "locks"), "batch": str(tree / "batch"),
        "trust_root": str(tree)}), encoding="utf-8")
    os.chmod(deploy_root / "roots.json", 0o400)
    env = dict(os.environ)
    env.update({
        "AKB2_HARVEST_DEPLOY_ROOT": str(deploy_root),
        "AKB2_HARVEST_DEPLOY_UID": str(uid),
        "AKB2_HARVEST_DEPLOY_GID": str(gid),
        "AKB2_HARVEST_RUNTIME_READER_GID": str(gid),
        "AKB2_HARVEST_RUNTIME_UID": str(uid),
        "AKB2_HARVEST_RUNTIME_GID": str(gid),
        # p1a-sc0:CLI 走 ledger.run_harvest_with_ledger 后账本经
        # resolve_state_file 落 AKB2_STATE_ROOT;不隔离会写真实家目录
        "AKB2_STATE_ROOT": str(tmp / "state"),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    })
    r = subprocess.run(
        [sys.executable, "scripts/harvest_anchor_bootstrap.py", "init",
         "--deployment-id", "g2-asm"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    fp = hashlib.sha256(
        (deploy_root / "root-key" / "root.pub").read_bytes()).hexdigest()
    env["AKB2_HARVEST_ROOT_PK_FINGERPRINT"] = fp
    return SimpleNamespace(deploy_root=deploy_root, tree=tree,
                           raw_dir=tree / "raw", env=env)


@pytest.fixture(scope="session")
def nested_deployment(
        tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """深层 raw_dir 布局:raw 在 trust_root/x/y/raw(父目录 != trust_root)。

    与 deployment 同构,仅目录层级不同——用于暴露「隔离目录经 parent_fd
    创建、归档 output_dir 却按 trust_root 拼路径」的错位缺陷。
    """
    tmp = tmp_path_factory.mktemp("harvest-deploy-nested")
    uid, gid = os.getuid(), os.getgid()
    deploy_root = tmp / "deploy"
    deploy_root.mkdir(0o750)
    tree = tmp / "tree"
    tree.mkdir(0o750)
    raw_dir = tree / "x" / "y" / "raw"
    # 中间级逐层显式 0750:parents=True 走 umask 会给出组/其他可写目录,
    # bootstrap 的父链权限校验拒「其他位可写」
    cur = tree
    for part in ("x", "y", "raw"):
        cur = cur / part
        cur.mkdir(0o750)
    for rel in ("stage", "locks", "batch"):
        (tree / rel).mkdir(0o750)
    (deploy_root / "roots.json").write_text(json.dumps({
        "raw_dir": str(raw_dir), "stage": str(tree / "stage"),
        "locks_dir": str(tree / "locks"), "batch": str(tree / "batch"),
        "trust_root": str(tree)}), encoding="utf-8")
    os.chmod(deploy_root / "roots.json", 0o400)
    env = dict(os.environ)
    env.update({
        "AKB2_HARVEST_DEPLOY_ROOT": str(deploy_root),
        "AKB2_HARVEST_DEPLOY_UID": str(uid),
        "AKB2_HARVEST_DEPLOY_GID": str(gid),
        "AKB2_HARVEST_RUNTIME_READER_GID": str(gid),
        "AKB2_HARVEST_RUNTIME_UID": str(uid),
        "AKB2_HARVEST_RUNTIME_GID": str(gid),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    })
    r = subprocess.run(
        [sys.executable, "scripts/harvest_anchor_bootstrap.py", "init",
         "--deployment-id", "g2-asm-nested"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    fp = hashlib.sha256(
        (deploy_root / "root-key" / "root.pub").read_bytes()).hexdigest()
    env["AKB2_HARVEST_ROOT_PK_FINGERPRINT"] = fp
    return SimpleNamespace(deploy_root=deploy_root, tree=tree,
                           raw_dir=raw_dir, env=env)


@pytest.fixture()
def nested_deploy_env(monkeypatch: pytest.MonkeyPatch,
                      nested_deployment: SimpleNamespace) -> None:
    for k, v in nested_deployment.env.items():
        monkeypatch.setenv(k, v)


def _iso_names(root: Path) -> list[str]:
    """root 目录下现存的全部隔离目录名(任意 run_id)。"""
    try:
        return sorted(n for n in os.listdir(root)
                      if n.startswith(".harvest-iso--"))
    except FileNotFoundError:
        return []


def _stray_md(root: Path) -> list[str]:
    """root 目录下现存的散落 markdown(隔离目录本不应出现在这里)。"""
    try:
        return sorted(n for n in os.listdir(root) if n.endswith(".md"))
    except FileNotFoundError:
        return []


@pytest.fixture()
def deploy_env(monkeypatch: pytest.MonkeyPatch,
               deployment: SimpleNamespace) -> None:
    for k, v in deployment.env.items():
        monkeypatch.setenv(k, v)


def _raw_before(deployment: SimpleNamespace) -> set[str]:
    return set(os.listdir(deployment.raw_dir))


def _new_raw(deployment: SimpleNamespace, before: set[str]) -> list[str]:
    return sorted(set(os.listdir(deployment.raw_dir)) - before)


def _parse_fm(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "缺少 frontmatter 块"
    return yaml.safe_load(m.group(1))


def _render_expected_control(p: dict, run_id: str,
                             deployment: SimpleNamespace) -> str:
    """测试自身现场生成对照产物(同输入同环境),返回最终期望文本。"""
    sm = p["source_meta"]
    ref_dir = deployment.tree / f".ref--{run_id}.tmp"
    ref_dir.mkdir(0o700)
    try:
        res = assembler.archive_raw(
            slug=p["session_id"], url=_url(p["session_id"]), title=p["title"],
            author=str(sm.get("author") or "unknown"),
            content=assembler._render_body(p),
            published_at=str(sm.get("published_at") or "unknown"),
            publisher=str(sm.get("publisher") or "unknown"),
            fetched_via="cindy-harvest",
            output_dir=ref_dir, date_str="2026-08-11")
        assert res.success
        return Path(res.output_path).read_text(encoding="utf-8")
    finally:
        for f in ref_dir.iterdir():
            f.unlink()
        ref_dir.rmdir()


# ---------------------------------------------------------------------------
# ① 集成 + (2a-2) 写盘所有权
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_lands_file_10_keys_anchors(self, deployment: SimpleNamespace,
                                        deploy_env: None,
                                        ids: SimpleNamespace) -> None:
        """真实 assemble:产出 raw 文件,10 键 frontmatter,AEHMZ 在场,run_id 逐字。"""
        before = _raw_before(deployment)
        filename = assembler.assemble(_payload(ids.session_id),
                                      harvest_run_id=ids.run_id)
        new = _new_raw(deployment, before)
        assert new == [filename]
        assert RAW_NAME_RE.match(filename), filename
        text = (deployment.raw_dir / filename).read_text(encoding="utf-8")
        fm = _parse_fm(text)
        assert set(fm) == FM_KEYS  # 9 既有字段 + harvest_run_id = 恰 10 键
        assert fm["harvest_run_id"] == ids.run_id
        assert fm["source_url"] == _url(ids.session_id)
        assert fm["fetched_via"] == "cindy-harvest"
        for key in REQUIRED_ANCHOR_KEYS:
            assert f"§{key}" in text, f"缺必需锚点 §{key}"
        assert "## 锚点目录" in text

    def test_archive_raw_once_with_iso_output_dir(self, deployment,
                                                  deploy_env: None,
                                                  ids: SimpleNamespace,
                                                  monkeypatch) -> None:
        """(2a-2)(A) archive_raw 恰一次且 output_dir 指向隔离目录(非 raw_dir)。"""
        calls: list[dict] = []
        orig = assembler.archive_raw

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return orig(*args, **kwargs)

        monkeypatch.setattr(assembler, "archive_raw", spy)
        assembler.assemble(_payload(ids.session_id), harvest_run_id=ids.run_id)
        assert len(calls) == 1
        out_dir = Path(calls[0]["output_dir"])
        assert out_dir.name.startswith(".harvest-iso--"), out_dir
        assert out_dir.parent == deployment.tree  # 同挂载点(trust_root 下)
        assert str(out_dir) != str(deployment.raw_dir)

    def test_landing_via_atomic_rename_no_intermediate(
            self, deployment, deploy_env: None, ids: SimpleNamespace,
            monkeypatch) -> None:
        """(2a-2)(C) 落位经 _place 恰一次;落位前 raw_dir 零新增、落位后只剩最终名。"""
        seen: list[set[str]] = []
        orig_place = assembler._place

        def spy_place(iso_fd, tmp_name, raw_fd, filename, reservation):
            seen.append(set(os.listdir(deployment.raw_dir)))  # 落位前快照
            return orig_place(iso_fd, tmp_name, raw_fd, filename, reservation)

        monkeypatch.setattr(assembler, "_place", spy_place)
        before = _raw_before(deployment)
        filename = assembler.assemble(_payload(ids.session_id),
                                      harvest_run_id=ids.run_id)
        assert len(seen) == 1
        assert seen[0] == before  # 落位前 raw_dir 零新增(无中间产物)
        assert _new_raw(deployment, before) == [filename]

    def test_clean_byte_equivalence(self, deployment, deploy_env: None,
                                    ids: SimpleNamespace,
                                    monkeypatch) -> None:
        """(2a)(ii) clean 支:最终产物剔除 run_id 行后 == 对照 archive_raw 产物。"""
        fixed = datetime(2026, 8, 11, 3, 30, 0, tzinfo=UTC)

        class _Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr("src.akb2.quality.intake.raw_archiver.datetime",
                            _Fixed)
        monkeypatch.setattr("akb2.harvest.assembler.datetime", _Fixed)
        before = _raw_before(deployment)
        filename = assembler.assemble(_payload(ids.session_id),
                                      harvest_run_id=ids.run_id)
        landed = (deployment.raw_dir / filename).read_text(encoding="utf-8")
        original = _render_expected_control(_payload(ids.session_id),
                                            ids.run_id, deployment)
        expected = assembler._append_harvest_run_id(original, ids.run_id)
        assert landed == expected  # 剔 run_id 行后逐字节等价,且只多了这一行
        assert _new_raw(deployment, before) == [filename]

    def test_redact_differential_against_original(self, deployment,
                                                  deploy_env: None,
                                                  ids: SimpleNamespace,
                                                  monkeypatch) -> None:
        """(2a)(ii) redact 支:最终产物 == 对照 + 具名替换 + run_id 一行。"""
        fixed = datetime(2026, 8, 11, 3, 30, 0, tzinfo=UTC)

        class _Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr("src.akb2.quality.intake.raw_archiver.datetime",
                            _Fixed)
        monkeypatch.setattr("akb2.harvest.assembler.datetime", _Fixed)
        before = _raw_before(deployment)
        p = _payload(ids.session_id, with_secret=True)
        filename = assembler.assemble(p, harvest_run_id=ids.run_id,
                                      policy=RedactionPolicy.Redact)
        landed = (deployment.raw_dir / filename).read_text(encoding="utf-8")
        original = _render_expected_control(p, ids.run_id, deployment)
        redacted, replacements = scan_and_redact(p, original,
                                                 policy=RedactionPolicy.Redact)
        assert replacements, "对照应含凭证命中"
        expected = assembler._append_harvest_run_id(redacted, ids.run_id)
        assert landed == expected
        assert REDACTED_MARKER in landed
        assert SECRET not in landed
        assert _new_raw(deployment, before) == [filename]

    def test_iso_cleaned_on_success(self, deployment, deploy_env: None,
                                    ids: SimpleNamespace) -> None:
        """(2b) 成功路径:隔离目录被清理(os.path.exists False)。"""
        iso = deployment.tree / f".harvest-iso--{ids.run_id}.tmp"
        assembler.assemble(_payload(ids.session_id), harvest_run_id=ids.run_id)
        assert not iso.exists()

    def test_iso_cleaned_on_failure(self, deployment, deploy_env: None,
                                    ids: SimpleNamespace,
                                    monkeypatch) -> None:
        """(2b) 失败路径(archive_raw 抛错):隔离目录已建但被清理,raw_dir 零新增。"""
        iso = deployment.tree / f".harvest-iso--{ids.run_id}.tmp"

        def boom(*args, **kwargs):
            raise OSError("注入的 archive_raw 失败")

        monkeypatch.setattr(assembler, "archive_raw", boom)
        before = _raw_before(deployment)
        with pytest.raises(OSError):
            assembler.assemble(_payload(ids.session_id),
                               harvest_run_id=ids.run_id)
        assert not iso.exists()
        assert _new_raw(deployment, before) == []

    def test_iso_cleaned_on_open_failure(self, deployment, deploy_env: None,
                                        ids: SimpleNamespace,
                                        monkeypatch) -> None:
        """隔离目录已创建但后续 open 失败时，不能留下孤儿目录。"""
        iso = deployment.tree / f".harvest-iso--{ids.run_id}.tmp"
        orig_open = assembler.os.open

        def boom_on_iso(name, flags, *args, **kwargs):
            # 只拦截隔离目录本身的 open:名字带 .harvest-iso-- 前缀,或是
            # 在 dir_fd 下打开目录(O_DIRECTORY)。anchors 的 trust_root open
            # 同样带 O_DIRECTORY 但无 dir_fd——不能误伤,否则前置链路先炸。
            is_iso_name = ".harvest-iso--" in str(name)
            is_dir_open = bool(kwargs.get("dir_fd")) and bool(flags & os.O_DIRECTORY)
            if is_iso_name or is_dir_open:
                raise OSError("注入的 open 失败")
            return orig_open(name, flags, *args, **kwargs)

        monkeypatch.setattr(assembler.os, "open", boom_on_iso)
        before = _raw_before(deployment)
        with pytest.raises(assembler.AssemblyRejectedError):
            assembler.assemble(_payload(ids.session_id),
                               harvest_run_id=ids.run_id)
        assert not iso.exists()
        assert _new_raw(deployment, before) == []


# ---------------------------------------------------------------------------
# (nested) 深层 raw_dir:隔离目录与归档 output_dir 必须同一目录
# ---------------------------------------------------------------------------


class TestNestedRawDir:
    """raw_dir=<trust_root>/x/y/raw 时,iso 经 parent_fd 建在 x/y 下,
    output_dir 不得错指 trust_root——错位会在 trust_root 残留明文 markdown。"""

    def test_success_lands_only_in_raw_dir(
            self, nested_deployment: SimpleNamespace,
            nested_deploy_env: None, ids: SimpleNamespace) -> None:
        """成功落位:产物只进 raw_dir;trust_root 与中间父目录无隔离目录、无散落 .md。"""
        nd = nested_deployment
        before = _raw_before(nd)
        filename = assembler.assemble(_payload(ids.session_id),
                                      harvest_run_id=ids.run_id)
        assert _new_raw(nd, before) == [filename]
        text = (nd.raw_dir / filename).read_text(encoding="utf-8")
        assert ids.run_id in text
        # trust_root 与 raw 的每级父目录:零隔离目录、零散落 markdown
        for probe in (nd.tree, nd.tree / "x", nd.tree / "x" / "y"):
            assert _iso_names(probe) == [], probe
            assert _stray_md(probe) == [], probe

    def test_secret_reject_leaves_no_residue(
            self, nested_deployment: SimpleNamespace,
            nested_deploy_env: None, ids: SimpleNamespace) -> None:
        """含 Bearer 凭证 + 默认 Reject:exit 3 语义,raw_dir 零新增,
        trust_root 与 raw 父目录无残留 .md / 隔离目录。"""
        nd = nested_deployment
        before = _raw_before(nd)
        with pytest.raises(RedactionRejectedError):
            assembler.assemble(_payload(ids.session_id, with_secret=True),
                               harvest_run_id=ids.run_id)
        assert _new_raw(nd, before) == []
        for probe in (nd.tree, nd.tree / "x", nd.tree / "x" / "y"):
            assert _iso_names(probe) == [], probe
            assert _stray_md(probe) == [], probe
        # 全树零 Bearer 明文(Reject 语义:未脱敏产物必须已销毁)
        for md in sorted(nd.tree.rglob("*.md")):
            assert SECRET not in md.read_text(encoding="utf-8"), md


# ---------------------------------------------------------------------------
# ③ run_id 贯通契约
# ---------------------------------------------------------------------------


class TestRunIdContract:
    def test_cli_missing_run_id_exit2(self, deployment: SimpleNamespace,
                                      deploy_env: None, ids: SimpleNamespace,
                                      tmp_path: Path) -> None:
        """省略 --harvest-run-id → exit 2 且零产物。"""
        f = tmp_path / "p.json"
        f.write_text(json.dumps(_payload(ids.session_id)), encoding="utf-8")
        env = dict(deployment.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        before = _raw_before(deployment)
        r = subprocess.run(
            [sys.executable, "-m", "akb2.harvest", str(f)],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        assert r.returncode == 2
        assert _new_raw(deployment, before) == []

    def test_cli_directory_input_exit2(self, deployment: SimpleNamespace,
                                      deploy_env: None, ids: SimpleNamespace,
                                      tmp_path: Path) -> None:
        """输入路径是目录时，CLI 也应收口为 exit 2 而非 traceback。"""
        d = tmp_path / "input-dir"
        d.mkdir()
        env = dict(deployment.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        before = _raw_before(deployment)
        r = subprocess.run(
            [sys.executable, "-m", "akb2.harvest", str(d),
             "--harvest-run-id", ids.run_id],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        assert r.returncode == 2
        assert "文件不可读" in r.stderr
        assert _new_raw(deployment, before) == []

    def test_function_missing_run_id_typeerror(self, deploy_env: None,
                                               ids: SimpleNamespace) -> None:
        """省略函数入口具名参数 → TypeError(两侧同等 fail-closed)。"""
        with pytest.raises(TypeError):
            assembler.assemble(_payload(ids.session_id))  # type: ignore[call-arg]

    def test_run_id_in_ledger_via_flow(self, deployment, deploy_env: None,
                                       ids: SimpleNamespace,
                                       tmp_path: Path) -> None:
        """端到端(真实 flow + 真实 assemble):committed 记录中 run_id 逐字可见。"""
        from akb2.harvest.flow import HarvestState, run_harvest_flow

        ledger = tmp_path / "ledger.jsonl"
        out = run_harvest_flow(
            _payload(ids.session_id), harvest_run_id=ids.run_id,
            raw_dir=Path(deployment.raw_dir),
            ledger_path=ledger,
            confirm=lambda _rendered: True,
            assembler=assembler.assemble,
        )
        assert out.state is HarvestState.SUBMITTED and out.submitted
        lines = [json.loads(line) for line in ledger.read_text().splitlines()
                 if line.strip()]
        committed = [e for e in lines if e.get("event") == "committed"]
        assert committed, "ledger 应含 committed 记录"
        assert committed[0]["harvest_run_id"] == ids.run_id

    def test_run_id_validation_identity(self) -> None:
        """CLI 与 assembler 共用同一 validate_harvest_run_id 函数对象。"""
        assert cli.validate_harvest_run_id is assembler.validate_harvest_run_id

    def test_bad_run_id_rejected_both_sides(self, deployment, deploy_env: None,
                                            ids: SimpleNamespace,
                                            tmp_path: Path) -> None:
        """路径逃逸双入口:函数侧与 CLI 侧对恶意 run_id 均拒,raw_dir 零新增。"""
        before = _raw_before(deployment)
        for bad in ("../../raw", f"{deployment.raw_dir}", "not-a-uuid"):
            with pytest.raises(assembler.AssemblyRejectedError):
                assembler.assemble(_payload(ids.session_id),
                                   harvest_run_id=bad)
        assert _new_raw(deployment, before) == []
        env = dict(deployment.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        f = tmp_path / "p.json"
        f.write_text(json.dumps(_payload(ids.session_id)), encoding="utf-8")
        for bad in ("../../raw", f"{deployment.raw_dir}", "not-a-uuid"):
            r = subprocess.run(
                [sys.executable, "-m", "akb2.harvest", str(f),
                 "--harvest-run-id", bad],
                capture_output=True, text=True, env=env, cwd=REPO_ROOT)
            assert r.returncode == 2
        assert _new_raw(deployment, before) == []


# ---------------------------------------------------------------------------
# (p) typed policy 三例
# ---------------------------------------------------------------------------


class TestTypedPolicy:
    def test_default_reject_cleans_iso(self, deployment, deploy_env: None,
                                       ids: SimpleNamespace) -> None:
        """(p)① 不传 policy → 默认 Reject:exit 3 语义、raw_dir 零新增、隔离产物已删。"""
        iso = deployment.tree / f".harvest-iso--{ids.run_id}.tmp"
        before = _raw_before(deployment)
        with pytest.raises(RedactionRejectedError):
            assembler.assemble(_payload(ids.session_id, with_secret=True),
                               harvest_run_id=ids.run_id)
        assert _new_raw(deployment, before) == []
        assert not iso.exists()  # 含未脱敏凭证的隔离产物必须已销毁

    def test_cli_redact_lands_and_rescan_clean(self, deployment,
                                               deploy_env: None,
                                               ids: SimpleNamespace,
                                               tmp_path: Path) -> None:
        """(p)② CLI --redact:落盘凭证已替换、终检再扫零命中、落盘==终检所扫。"""
        f = tmp_path / "p_secret.json"
        f.write_text(json.dumps(_payload(ids.session_id, with_secret=True)),
                     encoding="utf-8")
        env = dict(deployment.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        before = _raw_before(deployment)
        r = subprocess.run(
            [sys.executable, "-m", "akb2.harvest", str(f),
             "--harvest-run-id", ids.run_id, "--redact"],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        assert r.returncode == 0, r.stderr
        new = _new_raw(deployment, before)
        assert len(new) == 1
        text = (deployment.raw_dir / new[0]).read_text(encoding="utf-8")
        assert SECRET not in text
        assert REDACTED_MARKER in text
        # 终检再扫一遍且零命中;落位文件 == 终检所扫文本(本就是同一份)
        _out, again = scan_and_redact(_payload(ids.session_id), text,
                                      policy=RedactionPolicy.Reject)
        assert again == []

    def test_cli_zero_preprocessing(self) -> None:
        """(p)③ CLI 侧零写盘零替换:源码无写盘/替换调用;policy 取值域恰两值。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "__main__.py").read_text(
            encoding="utf-8")
        for banned in ("write_text", "os.open", "re.sub"):
            assert banned not in src, f"CLI 不应出现 {banned}"
        assert set(RedactionPolicy) == {RedactionPolicy.Reject,
                                        RedactionPolicy.Redact}
        assert len(RedactionPolicy) == 2


# ---------------------------------------------------------------------------
# (r) redaction 不可绕过
# ---------------------------------------------------------------------------


class TestRedactionUnbypassable:
    def test_three_credential_shapes_rejected(self, deployment,
                                              deploy_env: None,
                                              ids: SimpleNamespace) -> None:
        """(r)① token / API key / .env 三形态走 assemble 均 exit 3 语义、零落位。"""
        p1 = _payload(ids.session_id)
        p1["how_steps"][0]["cmd"] = f"curl -H 'Authorization: Bearer {SECRET}'"
        p2 = _payload(ids.session_id)
        p2["source_meta"]["api_key"] = "sk-abc1234567890abcdef"
        p3 = _payload(ids.session_id)
        p3["source_meta"]["client_secret"] = "deadbeefcafebabedeadbeefcafe"
        for p in (p1, p2, p3):
            before = _raw_before(deployment)
            with pytest.raises(RedactionRejectedError):
                assembler.assemble(p, harvest_run_id=ids.run_id)
            assert _new_raw(deployment, before) == []

    def test_scan_after_archive_raw_before_landing(self, deployment,
                                                   deploy_env: None,
                                                   ids: SimpleNamespace,
                                                   monkeypatch) -> None:
        """(r)② scan_and_redact 在 archive_raw 之后、任何落位之前。"""
        order: list[str] = []
        orig_ar = assembler.archive_raw
        orig_scan = assembler.scan_and_redact
        orig_place = assembler._place

        def spy_ar(*a, **k):
            order.append("archive_raw")
            return orig_ar(*a, **k)

        def spy_scan(*a, **k):
            order.append("scan_and_redact")
            return orig_scan(*a, **k)

        def spy_place(*a, **k):
            order.append("_place")
            return orig_place(*a, **k)

        monkeypatch.setattr(assembler, "archive_raw", spy_ar)
        monkeypatch.setattr(assembler, "scan_and_redact", spy_scan)
        monkeypatch.setattr(assembler, "_place", spy_place)
        assembler.assemble(_payload(ids.session_id), harvest_run_id=ids.run_id)
        assert order == ["archive_raw", "scan_and_redact", "_place"]

    def test_public_surface_no_skip(self) -> None:
        """(r)③ assemble 签名无跳过参数;公开面除 assemble 外无其它落盘入口。

        p1a-sc0 扩展:reservation/dest_filename/date_str 三注入参 keyword-only
        且默认 None——它们是 ledger 编排器的生产注入通道,不是 redaction
        跳过参数(policy 仍只有 Reject/Redact 两值,无法关闭扫描)。
        """
        sig = inspect.signature(assembler.assemble)
        params = sig.parameters
        assert set(params) == {"payload", "harvest_run_id", "policy",
                               "reservation", "dest_filename", "date_str"}
        assert params["harvest_run_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["harvest_run_id"].default is inspect.Parameter.empty
        assert params["policy"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["policy"].default is RedactionPolicy.Reject
        for injected in ("reservation", "dest_filename", "date_str"):
            assert params[injected].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[injected].default is None
        # dir() 扫描:assembler 自身定义的公开 callable 中,接受 payload 的
        # 只有 assemble(scan_and_redact 等是 redaction 模块导入,不落盘)
        for name in dir(assembler):
            if name.startswith("_") or name == "assemble":
                continue
            obj = getattr(assembler, name)
            if not callable(obj) or getattr(obj, "__module__", "") != \
                    "akb2.harvest.assembler":
                continue
            try:
                p = inspect.signature(obj).parameters
            except (TypeError, ValueError):
                continue
            assert "payload" not in p, f"{name} 是疑似绕过入口"


# ---------------------------------------------------------------------------
# (p1a-sc0) CLI → run_harvest_with_ledger 接线:①–⑦⑨–⑮
# ---------------------------------------------------------------------------


class _FixedDate(datetime):
    """钉死 cli.datetime 的 now():dest_filename 预计算与 main() 内部同日。

    跨午夜竞态防御:测试预计算 dest_filename 与 main() 内部各取一次
    now(),不钉死会在 23:59:59→00:00:00 边界 flaky。
    """

    _DAY = datetime(2031, 1, 2, 3, 30, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls._DAY


P1A_FIXED_DATE = "2031-01-02"


def _p1a_dest(sid: str) -> str:
    """与 cli.main 同一纯函数拼 dest_filename(须在 monkeypatch cli.datetime 后调用)。"""
    from akb2.harvest import __main__ as _cli
    day = _cli.datetime.now(UTC).strftime("%Y-%m-%d")
    return (f"{sid}--{compute_article_id('cindy://session/' + sid)}"
            f"--raw--{day}.md")


def _p1a_fingerprint(p: dict) -> str:
    """与 cli.main 同源指纹:sha256(_render_body(payload))。"""
    return hashlib.sha256(
        assembler._render_body(p).encode("utf-8")).hexdigest()


class TestCliLedgerWiring:
    """①②③④⑨⑬⑭ + ⑮ 的 main() 全链路分支(直接调 main,不经 subprocess)。"""

    @pytest.fixture()
    def p1a_env(self, deployment, deploy_env, monkeypatch, tmp_path):
        """部署 env + 独立状态根 + 钉死 cli.datetime。"""
        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.setattr(cli, "datetime", _FixedDate)
        return SimpleNamespace(state=tmp_path / "state")

    def _write_payload(self, tmp_path, p: dict, name: str = "p1a.json") -> Path:
        f = tmp_path / name
        f.write_text(json.dumps(p), encoding="utf-8")
        return f

    def test_created_writes_committed_row_with_real_abspath(
            self, deployment, ids, p1a_env, tmp_path, capsys):
        """① CLI 落位成功:lookup 返回 committed 行,raw_abspath == 实际落位
        文件(绝对路径且 basename==dest_filename,⑪ 闭包契约同证)。"""
        p = _payload(ids.session_id)
        f = self._write_payload(tmp_path, p)
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 0
        # ⑮ created 支:stderr 含 basename
        err = capsys.readouterr().err
        dest = _p1a_dest(ids.session_id)
        assert dest in err
        # ① committed 行 + raw_abspath 指向真实落位文件
        row = ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                ids.run_id)
        assert row is not None and row["event"] == "committed"
        landed = (deployment.raw_dir / dest)
        assert landed.is_file()
        # ⑪ raw_abspath 绝对路径、basename==dest_filename、指向实际文件
        assert Path(row["raw_abspath"]).is_absolute()
        assert Path(row["raw_abspath"]).name == dest
        assert Path(row["raw_abspath"]) == landed.resolve()

    def test_second_run_same_identity_already_committed(
            self, deployment, ids, p1a_env, tmp_path, capsys):
        """② 同身份同 run_id 二次执行:already_committed(exit 5),台账行数不增。"""
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        assert cli.main([str(f), "--harvest-run-id", ids.run_id]) == 0
        lp = ledger_mod.resolve_ledger_path()
        n1 = len(lp.read_text().splitlines())
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 5  # ⑮ already_committed 支
        assert "already_committed" in capsys.readouterr().err
        n2 = len(lp.read_text().splitlines())
        assert n2 == n1, "二次幂等跳过不得追加任何台账行"

    def test_same_run_id_different_identity_run_id_conflict(
            self, deployment, ids, p1a_env, tmp_path, capsys):
        """③ 同 run_id 不同身份(session 不同 → dest/fp 不同)→ run_id_conflict(6)。"""
        sid2 = "ffffffff-1111-4111-8111-111111111111"
        assert sid2 != ids.session_id
        f1 = self._write_payload(tmp_path, _payload(ids.session_id), "first.json")
        cli.main([str(f1), "--harvest-run-id", ids.run_id])
        f2 = self._write_payload(tmp_path, _payload(sid2), "second.json")
        rc = cli.main([str(f2), "--harvest-run-id", ids.run_id])
        assert rc == 6  # ⑮ run_id_conflict 支
        assert "run_id_conflict" in capsys.readouterr().err

    def test_redaction_reject_leaves_pending_no_committed(
            self, deployment, ids, p1a_env, tmp_path, capsys):
        """④a redaction Reject:exit 3 + 台账有 pending、无 committed。"""
        f = self._write_payload(tmp_path, _payload(ids.session_id,
                                                   with_secret=True))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 3  # ⑮ RedactionRejectedError 支
        assert "RedactionRejectedError" in capsys.readouterr().err
        row = ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                ids.run_id)
        assert row is not None and row["event"] == "pending"
        rows = ledger_mod.resolve_ledger_path().read_text().splitlines()
        assert all(json.loads(r)["event"] != "committed" for r in rows)

    def test_place_exists_leaves_pending_no_committed(
            self, deployment, ids, p1a_env, tmp_path, capsys):
        """④b _place 失败(预置同名目标 → EEXIST):exit 4 + pending 无 committed。"""
        dest = _p1a_dest(ids.session_id)
        (deployment.raw_dir / dest).write_text("预置占位", encoding="utf-8")
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 4  # ⑮ PlaceExistsError 支
        assert "PlaceExistsError" in capsys.readouterr().err
        row = ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                ids.run_id)
        assert row is not None and row["event"] == "pending"
        rows = ledger_mod.resolve_ledger_path().read_text().splitlines()
        assert all(json.loads(r)["event"] != "committed" for r in rows)

    def test_destname_conflict_exit4(self, deployment, ids, p1a_env,
                                     tmp_path, capsys):
        """⑮ destname_conflict 支:预持 DESTNAME 锁 → outcome=destname_conflict
        → exit 4(无 error 前缀,由 outcome 兜底)。"""
        dest = _p1a_dest(ids.session_id)
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        with locks_mod.reserve(locks_mod.DESTNAME, dest):
            rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 4
        assert "destname_conflict" in capsys.readouterr().err

    def test_content_conflict_exit2_named(self, deployment, ids, p1a_env,
                                          tmp_path, capsys):
        """⑮ content_conflict 支:预持 CONTENT 锁 → exit 2 且 stderr 点名
        content_conflict。"""
        p = _payload(ids.session_id)
        f = self._write_payload(tmp_path, p)
        ck = ledger_mod.content_key(ids.session_id, _p1a_fingerprint(p))
        with locks_mod.reserve(locks_mod.CONTENT, ck):
            rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2
        err = capsys.readouterr().err
        assert "content_conflict" in err
        # 不泄漏正文
        assert p["title"] not in err

    def test_first_callback_no_iso_and_single_assemble(
            self, deployment, ids, p1a_env, tmp_path, monkeypatch):
        """⑨ 第一次回调零副作用:spy 首次进入 assemble(= 第二次回调执行点,
        第一次回调已返回)时 tree 无 .harvest-iso--*;main 全程 assemble 恰一次。"""
        calls = {"n": 0}
        orig = assembler.assemble

        def spy(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                # 第一次进入 assemble 的时刻 = 编排器第一次回调已返回之后
                assert _iso_names(deployment.tree) == [], \
                    "第一次回调不得创建隔离目录"
            return orig(*a, **k)

        monkeypatch.setattr(assembler, "assemble", spy)
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 0
        assert calls["n"] == 1, "assemble() 进程内只被调用一次"
        assert _iso_names(deployment.tree) == []

    def test_preflight_error_structured_place_failed(
            self, deployment, ids, p1a_env, tmp_path, capsys, monkeypatch):
        """⑫ PreflightError 注入:异常不冒泡(traceback 零)、stderr 含
        'PreflightError:' 类型前缀、rc=2、台账 pending 无 committed。"""
        def boom(*a, **k):
            raise assembler.PreflightError("注入的预检失败")

        monkeypatch.setattr(assembler, "_preflight", boom)
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2
        err = capsys.readouterr().err
        assert "PreflightError:" in err
        row = ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                ids.run_id)
        assert row is not None and row["event"] == "pending"

    def test_assembly_rejected_via_injected_archive_failure(
            self, deployment, ids, p1a_env, tmp_path, capsys, monkeypatch):
        """⑮ AssemblyRejectedError 支:archive_raw 注入失败 → 前缀 → exit 2。"""
        def boom(*a, **k):
            return SimpleNamespace(success=False, output_path="",
                                   error="注入的 archive_raw 失败")

        monkeypatch.setattr(assembler, "archive_raw", boom)
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2
        assert "AssemblyRejectedError:" in capsys.readouterr().err

    def test_anchor_drift_via_injection_exit2(
            self, deployment, ids, p1a_env, tmp_path, capsys, monkeypatch):
        """⑮ AnchorDriftError 支:漂移注入 → 前缀 → exit 2。"""
        from akb2.harvest.anchors import AnchorDriftError

        def boom(h):
            raise AnchorDriftError("注入的锚点漂移")

        monkeypatch.setattr(assembler, "_verify_leaf_in_place", boom)
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2
        assert "AnchorDriftError:" in capsys.readouterr().err

    def test_raw_abspath_anchored_to_verified_raw_dir(
            self, deployment, ids, p1a_env, tmp_path):
        """⑬ raw_abspath 与 anchors 实际验证的 raw_dir 同一 inode(不从
        VAULT_ROOT 推导;若闭包改用 Path(vault)/filename 拼则此断言红)。"""
        import akb2.harvest.anchors as anchors_mod
        f = self._write_payload(tmp_path, _payload(ids.session_id))
        assert cli.main([str(f), "--harvest-run-id", ids.run_id]) == 0
        row = ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                ids.run_id)
        handles = anchors_mod.load_verified()
        try:
            st_fd = os.fstat(handles["raw_dir"].root_fd)
            st_path = os.stat(Path(row["raw_abspath"]).parent)
            assert (st_fd.st_dev, st_fd.st_ino) == (st_path.st_dev,
                                                    st_path.st_ino)
        finally:
            for h in handles.values():
                h.close()

    @pytest.mark.parametrize("mutate,label", [
        (lambda p: p.pop("session_id"), "缺 session_id"),
        (lambda p: p.update(session_id="NOT-A-UUID"), "session_id 非 UUID4"),
        (lambda p: p.update(session_id=12345), "session_id 非字符串"),
        (lambda p: p.update(how_steps=p["how_steps"][:4]), "lane 不足"),
        (lambda p: p.update(atoms=["not-a-dict"]), "atoms 元素非 dict"),
        (lambda p: p.update(how_steps=["not-a-dict"]), "how_steps 元素非 dict"),
    ])
    def test_identity_gate_rejects_before_ledger(
            self, deployment, ids, p1a_env, tmp_path, mutate, label):
        """⑭ identity 前置闸:六类坏 payload → exit 2 且 ledger.lookup 无行
        (零 pending 零 committed)。反证:拿掉前置闸后,坏 payload 要么在
        identity 计算处 KeyError 冒泡,要么先写 pending 再被后段拒——
        lookup 无行断言即转红。"""
        p = _payload(ids.session_id)
        mutate(p)
        f = self._write_payload(tmp_path, p)
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2, label
        assert ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                 ids.run_id) is None, label

    def test_identity_gate_non_dict_payload(self, deployment, ids, p1a_env,
                                            tmp_path):
        """⑭ payload 非 dict(JSON 数组)→ exit 2 零台账副作用。"""
        f = tmp_path / "arr.json"
        f.write_text("[1, 2, 3]", encoding="utf-8")
        rc = cli.main([str(f), "--harvest-run-id", ids.run_id])
        assert rc == 2
        assert ledger_mod.lookup(str(ledger_mod.resolve_ledger_path()),
                                 ids.run_id) is None

    def test_schema_gate_shares_function_objects(self):
        """⑭ 配套:CLI 前置闸与 assemble 内部同一函数对象(不另写闸)。"""
        assert cli.schema is schema_mod is assembler.schema
        assert cli.schema.validate is assembler.schema.validate
        assert cli.schema.run_lane_check is assembler.schema.run_lane_check


class TestOutcomeExitCodes:
    """⑮ 映射函数单测:main() 内不可达分支(assemble_failed/无前缀
    place_failed)经 _report_outcome 直测。"""

    @pytest.mark.parametrize("result,expected", [
        ({"outcome": "created", "filename": "x.md"}, 0),
        ({"outcome": "assemble_failed",
          "error": "RuntimeError: 注入"}, 2),
        ({"outcome": "place_failed", "error": "TypeError: 注入"}, 2),
        ({"outcome": "place_failed",
          "error": "AssemblyRejectedError: schema 校验失败"}, 2),
        ({"outcome": "place_failed",
          "error": "AnchorDriftError: 漂移"}, 2),
        ({"outcome": "place_failed",
          "error": "PreflightError: 预检"}, 2),
        ({"outcome": "place_failed",
          "error": "RedactionRejectedError: 命中"}, 3),
        ({"outcome": "place_failed",
          "error": "PlaceExistsError: 已存在"}, 4),
        ({"outcome": "destname_conflict"}, 4),
        ({"outcome": "already_committed"}, 5),
        ({"outcome": "run_id_conflict"}, 6),
        ({"outcome": "content_conflict"}, 2),
    ])
    def test_outcome_to_exit_code(self, result, expected):
        assert cli._report_outcome(result) == expected


class TestAssembleInjectionModes:
    """⑥⑦⑩:assemble 生产注入模式与 legacy 模式双模行为。"""

    @pytest.fixture()
    def inject_env(self, deployment, deploy_env, ids, monkeypatch):
        """部署 env + 真锁可用;返回预计算 dest(固定日期 2031-01-02)。"""
        dest = (f"{ids.session_id}--"
                f"{compute_article_id('cindy://session/' + ids.session_id)}"
                f"--raw--{P1A_FIXED_DATE}.md")
        return SimpleNamespace(dest=dest,
                               date_str=P1A_FIXED_DATE)

    def test_injected_reservation_skips_internal_reserve(
            self, deployment, ids, inject_env, monkeypatch):
        """⑥a 生产模式:传入有效凭证 → 不再内部 reserve DESTNAME(外部预约
        先于 spy 激活,spy 只见 assemble 内部调用)。"""
        reserve_calls: list[tuple[str, str]] = []
        orig_reserve = locks_mod.reserve

        def spy(ns, key):
            reserve_calls.append((ns, key))
            return orig_reserve(ns, key)

        p = _payload(ids.session_id)
        before = _raw_before(deployment)
        with locks_mod.reserve(locks_mod.DESTNAME, inject_env.dest) as rsv:
            monkeypatch.setattr(assembler.locks, "reserve", spy)
            raw = assembler.assemble(
                p, harvest_run_id=ids.run_id, reservation=rsv,
                dest_filename=inject_env.dest,
                date_str=inject_env.date_str)
        # assemble 内部零 DESTNAME 预约(凭证由外部持有)
        assert all(ns != locks_mod.DESTNAME for ns, _ in reserve_calls), \
            reserve_calls
        # 返回绝对路径,指向真实落位文件
        assert Path(raw).is_absolute()
        assert Path(raw).name == inject_env.dest
        assert Path(raw).is_file()
        assert _new_raw(deployment, before) == [inject_env.dest]

    def test_legacy_mode_reserves_internally_returns_basename(
            self, deployment, ids, inject_env, monkeypatch):
        """⑥b legacy 模式(三参缺省):内部自行 reserve+_place,返回 basename,
        行为与 origin/main 现网一致。"""
        reserve_calls: list[tuple[str, str]] = []
        orig_reserve = locks_mod.reserve

        def spy(ns, key):
            reserve_calls.append((ns, key))
            return orig_reserve(ns, key)

        monkeypatch.setattr(assembler.locks, "reserve", spy)
        before = _raw_before(deployment)
        filename = assembler.assemble(_payload(ids.session_id),
                                      harvest_run_id=ids.run_id)
        assert (locks_mod.DESTNAME, filename) in reserve_calls
        assert "/" not in filename, "legacy 返回 basename,非路径"
        assert _new_raw(deployment, before) == [filename]

    def test_partial_injection_rejected(self, deployment, ids, inject_env):
        """模式判别:三注入参部分提供 → TypeError(禁止一语双义)。"""
        p = _payload(ids.session_id)
        with locks_mod.reserve(locks_mod.DESTNAME, inject_env.dest) as rsv:
            with pytest.raises(TypeError):
                assembler.assemble(p, harvest_run_id=ids.run_id,
                                   reservation=rsv)  # 缺 dest_filename/date_str

    def test_released_reservation_rejected_no_landing(
            self, deployment, ids, inject_env):
        """⑦a 已 release 的 reservation → 拒(InvalidReservation)且 raw_dir
        无新文件。"""
        rsv = locks_mod.reserve(locks_mod.DESTNAME, inject_env.dest)
        with rsv:
            pass  # 进出即释放
        before = _raw_before(deployment)
        with pytest.raises(locks_mod.InvalidReservation):
            assembler.assemble(_payload(ids.session_id),
                               harvest_run_id=ids.run_id, reservation=rsv,
                               dest_filename=inject_env.dest,
                               date_str=inject_env.date_str)
        assert _new_raw(deployment, before) == []

    def test_wrong_namespace_reservation_rejected_no_landing(
            self, deployment, ids, inject_env):
        """⑦b 错误 namespace 的 reservation(CONTENT 键冒充 DESTNAME)→ 拒
        且 raw_dir 无新文件。"""
        with locks_mod.reserve(locks_mod.CONTENT, inject_env.dest) as rsv:
            before = _raw_before(deployment)
            with pytest.raises(locks_mod.InvalidReservation):
                assembler.assemble(_payload(ids.session_id),
                                   harvest_run_id=ids.run_id, reservation=rsv,
                                   dest_filename=inject_env.dest,
                                   date_str=inject_env.date_str)
            assert _new_raw(deployment, before) == []

    def test_dest_filename_matches_output_and_lock_key(
            self, deployment, ids, inject_env):
        """⑩ 预计算 dest_filename == archive_raw 产出 basename == DESTNAME
        锁键:注入固定远期日期 2031-01-02,落位文件名带同一日期(证明 date_str
        注入生效、assemble 未再 now());锁键即 dest_filename(_place 五项
        校验通过即证)。人为改 assemble 内部 date/article_id 后,产物名 ≠
        dest_filename,显式断言先拒 → 本用例红。"""
        p = _payload(ids.session_id)
        before = _raw_before(deployment)
        with locks_mod.reserve(locks_mod.DESTNAME, inject_env.dest) as rsv:
            raw = assembler.assemble(
                p, harvest_run_id=ids.run_id, reservation=rsv,
                dest_filename=inject_env.dest,
                date_str=inject_env.date_str)
        assert Path(raw).name == inject_env.dest
        assert _new_raw(deployment, before) == [inject_env.dest]
        assert P1A_FIXED_DATE in inject_env.dest
        # 锁键 == dest_filename 已由 reserve(DESTNAME, dest) + _place
        # assert_valid 通过共同证明;frontmatter run_id 逐字
        text = (deployment.raw_dir / inject_env.dest).read_text(
            encoding="utf-8")
        assert f"harvest_run_id: {ids.run_id}" in text

    def test_mismatched_dest_filename_rejected_before_place(
            self, deployment, ids, inject_env):
        """⑩ 负例:传入与 archive_raw 产物必然不符的 dest_filename →
        AssemblyRejectedError 且不 _place(raw_dir 零新增)。"""
        wrong = (f"{ids.session_id}--{'0' * 12}"
                 f"--raw--{P1A_FIXED_DATE}.md")
        with locks_mod.reserve(locks_mod.DESTNAME, wrong) as rsv:
            before = _raw_before(deployment)
            with pytest.raises(assembler.AssemblyRejectedError):
                assembler.assemble(_payload(ids.session_id),
                                   harvest_run_id=ids.run_id,
                                   reservation=rsv, dest_filename=wrong,
                                   date_str=inject_env.date_str)
            assert _new_raw(deployment, before) == []
