"""synthesis_assembler — 1对多合成提炼的 raw 装配层(F47)。

用户裁决(2026-07-12):单篇料薄的文章 1:1 重考是浪费——同主题簇的多篇
"过准入但死于地板/评分"的文章,机制组件与因果证据**聚合后**足以过全部硬门。

设计核心:**1对多发生在 raw 装配层,管线零改动**。
  - 同主题簇(2-5 篇)的成员 raw 正文逐字搬运(剥掉各自旧锚点骨架),按
    "## 来源N" 分节拼装,每节头部标注来源标题/URL/成员 raw 文件名(溯源链);
  - 组合正文交给 raw_archiver.archive_raw 走**完全相同**的锚点注入 +
    frontmatter + fail-closed 断言,产出一份合成 raw 落入 raw/ 目录;
  - daemon 自然扫描 → admission → distill → 地板 → 评分 → 五闸,
    **判定链一根不动**——合成内容照样要真刀真枪过全部标准。

宁缺勿假红线:装配只做逐字搬运与出处标注,零改写零缩写零编造;
成员清单/来源 URL 写进每节正文(锚点指向合成 raw,合成 raw 每节自带出处,
溯源链完整)。

有界(宪法 P1):SYNTHESIS_MIN_MEMBERS=2 / SYNTHESIS_MAX_MEMBERS=5;
CLI 手动触发(manual 类,不注册自动化触发器)。

CLI:
    # 从失败池建议同主题簇(确定性 token 重合聚类,零 LLM)
    python -m akb2.quality.synthesis_assembler --suggest

    # 装配一簇
    python -m akb2.quality.synthesis_assembler \
        --members a--raw--D.md b--raw--D.md --topic agent-memory
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.akb2.quality.intake.raw_archiver import ArchiveResult, archive_raw

# 有界预算(宪法 P1 具名)
SYNTHESIS_MIN_MEMBERS: int = 2
SYNTHESIS_MAX_MEMBERS: int = 5
# suggest 聚类参数:标题关键词 Jaccard 阈值(确定性,零 LLM)
SUGGEST_JACCARD_MIN: float = 0.25
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{3,}")
_STOPWORDS = frozenset({
    "with", "from", "this", "that", "your", "what", "when", "into", "using",
    "guide", "blog", "post", "2024", "2025", "2026", "part", "how",
})

# 成员 raw 清洗:剥旧锚点骨架(合成 raw 会注入全新一致的锚点集)
_ANCHOR_HEADING_RE = re.compile(r"^#{1,6}\s*§[A-Z][A-Z0-9_]*.*$")
_ANCHOR_TOC_HEADING_RE = re.compile(r"^#{1,6}\s*锚点目录\s*$")
_ANCHOR_TOC_ROW_RE = re.compile(r"^\|\s*§[A-Z]")


def _parse_member(raw_path: Path) -> dict[str, str]:
    """读成员 raw:提取 frontmatter 元数据 + 剥锚点骨架后的正文。

    fail-closed:无 frontmatter / 正文为空 → ValueError(不装配残料)。
    """
    text = raw_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise ValueError(f"成员无 frontmatter: {raw_path.name}")
    fm_text = m.group(1)

    def _field(name: str) -> str:
        fm_m = re.search(rf"^{name}:\s*(.*)$", fm_text, re.M)
        return fm_m.group(1).strip().strip('"').strip("'") if fm_m else ""

    body_lines: list[str] = []
    in_toc = False
    for line in text[m.end():].splitlines():
        if _ANCHOR_TOC_HEADING_RE.match(line):
            in_toc = True
            continue
        if in_toc:
            # 锚点目录块:表格行/分隔行/空行都吞,遇到正文行退出
            if _ANCHOR_TOC_ROW_RE.match(line) or line.startswith("|") or not line.strip():
                continue
            in_toc = False
        if _ANCHOR_HEADING_RE.match(line):
            continue  # 旧锚点标题行剥除(保留其下正文)
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if not body:
        raise ValueError(f"成员正文为空: {raw_path.name}")
    return {
        "filename": raw_path.name,
        "slug": _field("source_slug") or raw_path.name.split("--")[0],
        "title": _field("source_title") or raw_path.stem,
        "url": _field("source_url"),
        "body": body,
    }


def _synthesis_identity(members: list[dict[str, str]], topic: str) -> str:
    """合成 raw 的确定性 URI(同簇同 topic → 同 article_id,幂等)。"""
    key = "|".join(sorted(m["filename"] for m in members))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"synthesis://{topic}/{digest}"


def assemble_synthesis(
    member_paths: list[Path],
    topic: str,
    output_dir: Path,
    failures_path: str | None = None,
    date_str: str | None = None,
) -> ArchiveResult:
    """装配同主题簇为一份合成 raw(复用 archive_raw 全部规范与断言)。

    Returns:
        ArchiveResult(archiver 原样返回;锚点断言不过 → 不写文件 fail-closed)。
    """
    if not (SYNTHESIS_MIN_MEMBERS <= len(member_paths) <= SYNTHESIS_MAX_MEMBERS):
        raise ValueError(
            f"成员数 {len(member_paths)} 超界(须 {SYNTHESIS_MIN_MEMBERS}"
            f"-{SYNTHESIS_MAX_MEMBERS},宪法 P1 有界)"
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", topic):
        raise ValueError(f"topic 须 kebab-case: {topic!r}")

    members = [_parse_member(p) for p in member_paths]

    sections: list[str] = []
    for i, mem in enumerate(members, 1):
        sections.append(
            f"## 来源{i}: {mem['title']}\n\n"
            f"> 出处: {mem['url'] or '(无 URL)'} · 成员 raw: {mem['filename']}\n\n"
            f"{mem['body']}"
        )
    combined = "\n\n".join(sections)

    title = f"主题综述: {topic}(合成自 {len(members)} 篇同主题来源)"
    from datetime import UTC, datetime
    _pub = date_str or datetime.now(UTC).strftime("%Y-%m-%d")
    return archive_raw(
        slug=f"synthesis-{topic}",
        url=_synthesis_identity(members, topic),
        title=title,
        author="AKB2 synthesis assembler",
        content=combined,
        published_at=_pub,  # 装配日(合成 raw 的诞生时点,9 字段 fail-closed 要求)
        publisher="akb2-synthesis",
        fetched_via="synthesis_assembler",
        output_dir=output_dir,
        failures_path=failures_path,
        date_str=date_str,
    )


# ---------------------------------------------------------------------------
# suggest:失败池确定性聚类(零 LLM)
# ---------------------------------------------------------------------------


def _title_tokens(title: str) -> frozenset[str]:
    toks = {t.lower() for t in _TOKEN_RE.findall(title)}
    return frozenset(t for t in toks if t not in _STOPWORDS)


def suggest_clusters(
    ledger_path: Path,
    raw_dir: Path,
    compute_hash_fn: Any = None,
) -> list[list[str]]:
    """从失败池(过准入但 abort,无 finalize)按标题 token 重合建议同主题簇。

    确定性零 LLM:贪心配对,token Jaccard ≥ SUGGEST_JACCARD_MIN 归同簇,
    簇大小截到 SYNTHESIS_MAX_MEMBERS。仅建议,不装配(装配是人/lead 显式动作)。
    """
    if compute_hash_fn is None:
        from src.akb2.quality.runner import _compute_raw_hash as compute_hash_fn

    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if ledger_path.exists():
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events[e.get("content_hash", "")[:12]].append(e)

    failed_pool: set[str] = set()
    for h, evs in events.items():
        names = [e.get("event") for e in evs]
        if "finalize" in names or "intake_raw" not in names:
            continue
        last = evs[-1]
        if last.get("event") != "abort":
            continue
        r = str(last.get("details", {}).get("error")
                or last.get("details", {}).get("reason") or "")
        if "准入未通过" in r:
            continue  # 没过准入的不进合成池(主题/体裁不合格,合并也不合格)
        failed_pool.add(h)

    candidates: list[tuple[str, frozenset[str]]] = []
    for fn in sorted(os.listdir(raw_dir)):
        if not fn.endswith(".md") or fn.startswith("synthesis-"):
            continue
        try:
            h = compute_hash_fn(str(raw_dir / fn))[:12]
        except Exception:
            continue
        if h not in failed_pool:
            continue
        try:
            mem = _parse_member(raw_dir / fn)
        except ValueError:
            continue
        toks = _title_tokens(mem["title"])
        if toks:
            candidates.append((fn, toks))

    clusters: list[list[str]] = []
    used: set[str] = set()
    for fn, toks in candidates:
        if fn in used:
            continue
        cluster = [fn]
        used.add(fn)
        for fn2, toks2 in candidates:
            if fn2 in used or len(cluster) >= SYNTHESIS_MAX_MEMBERS:
                continue
            union = toks | toks2
            if union and len(toks & toks2) / len(union) >= SUGGEST_JACCARD_MIN:
                cluster.append(fn2)
                used.add(fn2)
        if len(cluster) >= SYNTHESIS_MIN_MEMBERS:
            clusters.append(cluster)
    return clusters


# ---------------------------------------------------------------------------
# CLI(manual 触发类;不注册任何自动化触发器)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F47 合成提炼 raw 装配器")
    parser.add_argument("--suggest", action="store_true", help="从失败池建议同主题簇")
    parser.add_argument("--members", nargs="*", default=[], help="成员 raw 文件名(raw/ 内)")
    parser.add_argument("--topic", default="", help="主题 slug(kebab-case)")
    parser.add_argument("--vault-root", default=os.environ.get("AKB2_VAULT_ROOT", ""))
    args = parser.parse_args(argv)

    if not args.vault_root:
        print("ERROR: 需 --vault-root 或 AKB2_VAULT_ROOT(fail-closed)", file=sys.stderr)
        return 1
    vault = Path(args.vault_root)
    raw_dir = vault / "raw"
    # 与 runner / promote 共用同一份账本：读 vault 旧账本会让同主题簇建议基于
    # 半份历史（runner 的入库事件已落到机器本地状态根）。
    from src.akb2.quality.promote import _resolve_ledger_path

    ledger = _resolve_ledger_path(vault, "quality-ledger.jsonl")

    if args.suggest:
        clusters = suggest_clusters(ledger, raw_dir)
        print(f"建议同主题簇 {len(clusters)} 个:")
        for i, c in enumerate(clusters, 1):
            print(f"  [{i}] {len(c)} 篇:")
            for fn in c:
                print(f"      {fn}")
        return 0

    if not args.members or not args.topic:
        print("ERROR: 装配需 --members + --topic(或用 --suggest)", file=sys.stderr)
        return 1
    paths = [raw_dir / m for m in args.members]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        print(f"ERROR: 成员不存在: {missing}", file=sys.stderr)
        return 1
    result = assemble_synthesis(
        paths, args.topic, output_dir=raw_dir,
        failures_path=str(_resolve_ledger_path(vault, "failures.jsonl")),
    )
    if result.success:
        print(f"[SYNTHESIS] 合成 raw 已落盘: {result.output_path}"
              f"(锚点 {result.anchor_count},daemon 将自然扫描入链)")
        return 0
    print(f"ERROR: 装配失败(fail-closed 未写文件): "
          f"missing_anchors={result.missing_anchors} error={result.error}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
