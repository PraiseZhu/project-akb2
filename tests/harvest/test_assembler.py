"""sc-asm-cli 验收:组装落盘主链路 + (p) typed policy 三例 + (r) redaction 不可绕过。

集成断言用**真实** bootstrap 部署根 + 真实 archive_raw / anchors / rename
(非 monkeypatch 替身);monkeypatch 只用于计数与次序观测。

覆盖(packet sc-asm-cli expect + sc-asm-redact expect):
  ① 集成:文件名 / 10 键 frontmatter / AEHMZ 锚点 / run_id 逐字
  (2a)(2a-2) 写盘所有权:archive_raw 恰一次且 output_dir 指向隔离目录;
     落位经原子改名恰一次;raw_dir 无中间产物
  (2a)(ii) clean/redact 两支逐字节比对(对照产物现场生成,同输入同环境)
  (2b) 隔离目录成功与失败两路径清理
  ③ run_id 贯通:CLI 缺失 exit 2 / 函数缺参 TypeError / ledger committed 逐字
  (p) 默认 Reject 零落位+隔离产物已销毁;CLI --redact 落盘+终检零命中+
     落盘==终检所扫;CLI 侧零写盘零替换
  (r) 三种凭证形态 exit 3 语义;scan 在 archive_raw 之后、落位之前;公开面无绕过
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

from akb2.harvest.redaction import (  # noqa: E402
    REDACTED_MARKER,
    RedactionPolicy,
    RedactionRejectedError,
    scan_and_redact,
)

from akb2.harvest import __main__ as cli  # noqa: E402
from akb2.harvest import assembler  # noqa: E402

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
            if flags & os.O_DIRECTORY:
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
        """(r)③ assemble 签名无跳过参数;公开面除 assemble 外无其它落盘入口。"""
        sig = inspect.signature(assembler.assemble)
        params = sig.parameters
        assert set(params) == {"payload", "harvest_run_id", "policy"}
        assert params["harvest_run_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["harvest_run_id"].default is inspect.Parameter.empty
        assert params["policy"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["policy"].default is RedactionPolicy.Reject
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
