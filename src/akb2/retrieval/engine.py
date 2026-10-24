"""AKB2 只读检索引擎：主链路已升格(promoted)笔记的关键词检索。

只读契约：本模块不写 vault 任何字节；vault 根由调用方解析（CLI 经
akb2.loops.paths.get_akb2_root —— env 优先、config/vault.toml 兜底，
constitution P5 禁硬编码），引擎只接受已解析的 Path。

promoted 样本集 exact 口径（来源：旧主链路 src/akb2/quality/promote.py 的 _flip_frontmatter；
该文件已于 2026-10-24 随主链路删除，口径冻结，由 tests/retrieval/test_promoted_predicate.py 钉住）：
- 落点 wiki/sources/<slug>.md（promoted 唯一写入点）
- frontmatter 行首四要素缺一不可：
  page_status 或 status = active + gate_decision = active
  + reviewer = akb2-quality-loop-v1

staging 候选（status: candidate / proposed_gate_decision: active）与隔离区
内容永不进入结果：扫描域只有 wiki/sources/，且行首锚定（^gate_decision:）
使 staging 的 proposed_gate_decision: active 不被误匹配。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

# 机器 reviewer 身份（旧主链路 promote.py 的 MACHINE_REVIEWER 值，现存已升格笔记都带它）
MACHINE_REVIEWER = "akb2-quality-loop-v1"

# promoted 笔记唯一落点（vault 相对）
SOURCES_SUBDIR = Path("wiki") / "sources"

# 索引页由收尾段维护（golden-sample §11），不是五闸升格的 source 笔记
_INDEX_FILENAME = "00-Index.md"

# frontmatter 判定全部行首锚定（同 promote._flip_frontmatter 的 re.M 口径）
_PAGE_STATUS_ACTIVE_RE = re.compile(r"^page_status:\s*active\s*$", re.M)
_STATUS_ACTIVE_RE = re.compile(r"^status:\s*active\s*$", re.M)
_GATE_DECISION_ACTIVE_RE = re.compile(r"^gate_decision:\s*active\s*$", re.M)
_REVIEWER_LOOP_RE = re.compile(rf"^reviewer:\s*{re.escape(MACHINE_REVIEWER)}\s*$", re.M)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TITLE_RE = re.compile(r"^title:\s*(.*?)\s*$", re.M)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_RELATED_BLOCK_RE = re.compile(
    r"^related_concepts:\s*(.*(?:\n(?:[ \t]*-[ \t]|[ \t]+#).*)*)",
    re.M,
)
_RELATED_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_RELATED_QUOTED_RE = re.compile(r"""(['"])(.*?)\1""")
_RELATED_BARE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

# How 节（How 密度内容载体）：新链路 = ## 论证与解释 下的 ### 操作步骤
# （与 quality/checks_structural._how_section_text 同口径）；历史 golden
# 样张形态 = 标题含 "(How 模板)" 的 ## 节（golden-sample 步骤 3）。
_HOW_SUBSECTION_RE = re.compile(r"^###\s*操作步骤\s*$")
_HOW_TEMPLATE_HEADING_RE = re.compile(r"^##\s*.*\(How 模板\)\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s")

# How 可执行性内容载体：How 节内的 inline 反引号命令（跳过 code fence 行）
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_CODE_FENCE_PREFIX = "```"

# 单笔记读取预算（字节）：超预算文件按「超大文件」形状跳过并显式计数，
# 不整读进内存（sc-engine-input-shapes；预算显式化，constitution P4）。
MAX_NOTE_BYTES = 2 * 1024 * 1024


class SkipReason(str):
    """单条笔记被跳过的原因（结果里可见地计数，不静默吞）。"""


SKIP_FRONTMATTER = SkipReason("frontmatter_unparsable")
SKIP_OVERSIZE = SkipReason("oversize")
SKIP_NON_UTF8 = SkipReason("non_utf8")


@dataclass(frozen=True)
class SearchResult:
    """单条检索结果（CLI JSON 输出的契约字段）。

    path/how_executability 之外的固定契约：how_density = How 节全文
    （How 密度维度的内容载体），how_executability = How 节内可执行命令清单
    （How 可执行性维度的内容载体）。字段名固定，供 MCP 化(p2)与端到端(p4)消费。
    """

    path: str
    title: str
    how_density: str
    how_executability: list[str]
    score: int
    related_concepts: list[str]
    graph_links: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def is_promoted(fm_text: str) -> bool:
    """frontmatter 是否满足 promoted 四要素（lead 2026-08-15 拍板口径）。"""
    status_active = bool(
        _PAGE_STATUS_ACTIVE_RE.search(fm_text) or _STATUS_ACTIVE_RE.search(fm_text)
    )
    return (
        status_active
        and bool(_GATE_DECISION_ACTIVE_RE.search(fm_text))
        and bool(_REVIEWER_LOOP_RE.search(fm_text))
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    """切出 (frontmatter, body)；无 frontmatter 返回 ("", text)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def _extract_title(fm_text: str, fallback: str) -> str:
    m = _TITLE_RE.search(fm_text)
    if not m:
        return fallback
    title = m.group(1).strip().strip('"').strip("'").strip()
    return title or fallback


def _normalize_graph_token(raw: str) -> str:
    token = raw.strip().strip('"').strip("'").strip()
    if token.startswith("[[") and token.endswith("]]"):
        token = token[2:-2]
    token = token.split("|", 1)[0].split("#", 1)[0].strip()
    if token.startswith("concept-"):
        return token
    # related_concepts 里常见 loop_engineering 这种下划线标识，升成 concept-*
    if "_" in token and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
        return f"concept-{token.replace('_', '-')}"
    return token


def _related_raw_tokens(blob: str) -> list[str]:
    """从 related_concepts 值里抽出完整 token：wikilink 优先，不拆 alias 单词。"""
    tokens: list[str] = []
    consumed: list[tuple[int, int]] = []
    for match in _RELATED_WIKILINK_RE.finditer(blob):
        tokens.append(match.group(1))
        consumed.append(match.span())
    for match in _RELATED_QUOTED_RE.finditer(blob):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        tokens.append(match.group(2))
        consumed.append(match.span())
    for match in _RELATED_BARE_RE.finditer(blob):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        tokens.append(match.group(0))
    return tokens


def extract_graph_fields(fm_text: str, body: str) -> tuple[list[str], list[str]]:
    """抽出 related_concepts 与正文 [[wikilink]]，去重保序。

    related_concepts 同时认 flow 单行与 YAML block list；wikilink alias
    （[[loop_engineering|Loop Engineering]]）只取竖线左侧 id。
    """
    related: list[str] = []
    seen_related: set[str] = set()
    related_match = _RELATED_BLOCK_RE.search(fm_text or "")
    if related_match:
        for item in _related_raw_tokens(related_match.group(1)):
            token = _normalize_graph_token(item)
            if token and token not in seen_related:
                seen_related.add(token)
                related.append(token)

    links: list[str] = []
    seen_links: set[str] = set()
    for match in _WIKILINK_RE.finditer(f"{fm_text or ''}\n{body or ''}"):
        token = _normalize_graph_token(match.group(1))
        if token and token not in seen_links:
            seen_links.add(token)
            links.append(token)
    return related, links


def _fence_state_table(lines: list[str]) -> list[bool]:
    """逐行预计算「该行是否位于围栏代码块内」，一次线性扫描。

    与 _section_after 的行内 fence 判定同一规则：以 ``` 开头的行是围栏
    开关（开关行自身记为 fence 外的边界态 False，块内行 True）。起始
    标题扫描用它过滤伪标题，保证起始与结束两侧共用一套 fence 状态机，
    不存在「结束认 fence、起始不认」的不对称。
    """
    states: list[bool] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith(_CODE_FENCE_PREFIX):
            in_fence = not in_fence
            states.append(False)  # 开关行本身不是块内伪标题来源
            continue
        states.append(in_fence)
    return states


def _section_after(lines: list[str], heading_idx: int) -> str:
    """取标题行之后到下一标题行之前的正文（strip 首尾空白）。

    围栏代码块（``` ... ```）内的行永不当作标题行判定：操作步骤里常见的
    脚本注释（如 `# backup first`）若被误判为下一节标题，会静默截断
    How 节正文——比抛错更危险（fail-closed 原则要求失败要看得见，不是
    看似成功、实则半截）。
    """
    out: list[str] = []
    in_fence = False
    for line in lines[heading_idx + 1:]:
        if line.lstrip().startswith(_CODE_FENCE_PREFIX):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _HEADING_RE.match(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def extract_how_section(body: str) -> str:
    """提取 How 节全文。

    优先 ### 操作步骤（新链路），兜底 ## xxx(How 模板)（历史样张）；
    都没有返回空串（promoted 笔记缺 How 节时 How 字段为空，不编造）。

    起始标题扫描与 _section_after 的结束扫描共用同一 fence 状态机：
    围栏代码块里的伪标题（如示例 markdown 里的 `### 操作步骤`）不是文档
    结构，不能当 How 起点——否则截出来的正文以围栏残留内容开头。

    fence 状态一次线性预计算后两轮扫描共用——不得对每行从文件头重扫
    前缀（O(n²)：MAX_NOTE_BYTES=2MiB 下约百万短行会拖垮整个检索）。
    """
    lines = body.splitlines()
    fenced = _fence_state_table(lines)
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if _HOW_SUBSECTION_RE.match(line):
            return _section_after(lines, i)
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if _HOW_TEMPLATE_HEADING_RE.match(line):
            return _section_after(lines, i)
    return ""


def extract_executable_commands(how_text: str) -> list[str]:
    """提取 How 节内 inline 反引号命令（去重保序，跳过整个 code fence 块）。

    围栏代码块内部的反引号跨度（如示例代码里的 `` `foo.bar()` ``）不是
    「操作步骤」编号行里的可执行命令，只跳过 fence 分隔符行本身不够——
    块内容同样要排除，否则会把示例文本误判为可执行命令。
    """
    commands: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for line in how_text.splitlines():
        if line.lstrip().startswith(_CODE_FENCE_PREFIX):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in _INLINE_CODE_RE.finditer(line):
            cmd = m.group(1).strip()
            if cmd and cmd not in seen:
                seen.add(cmd)
                commands.append(cmd)
    return commands


@dataclass(frozen=True)
class NoteDocument:
    """单篇 promoted 笔记全文（HTTP / 插件 get 的契约）。"""

    path: str
    title: str
    body: str
    how_density: str
    how_executability: list[str]
    related_concepts: list[str]
    graph_links: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _safe_sources_path(root: Path, rel_path: str) -> Path | None:
    """把 vault 相对路径解析到 wiki/sources/ 内的真实文件；越界或畸形返回 None。"""
    if not rel_path or "\x00" in rel_path:
        return None
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if candidate.suffix != ".md":
        return None
    sources_dir = (root / SOURCES_SUBDIR).resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(sources_dir)
    except ValueError:
        return None
    if target.name == _INDEX_FILENAME:
        return None
    if not target.is_file():
        return None
    return target


def read_promoted(root: Path, rel_path: str) -> NoteDocument | None:
    """读取一篇 promoted 笔记全文。非 promoted / 越界 / 超预算一律 None。"""
    target = _safe_sources_path(root, rel_path)
    if target is None:
        return None
    try:
        if target.stat().st_size > MAX_NOTE_BYTES:
            return None
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm_text, body = _split_frontmatter(text)
    if not fm_text or not is_promoted(fm_text):
        return None
    title = _extract_title(fm_text, target.stem)
    how_text = extract_how_section(body)
    related, links = extract_graph_fields(fm_text, body)
    return NoteDocument(
        path=target.relative_to(root).as_posix(),
        title=title,
        body=body,
        how_density=how_text,
        how_executability=extract_executable_commands(how_text),
        related_concepts=related,
        graph_links=links,
    )


def search(root: Path, query: str, limit: int | None = None) -> list[SearchResult]:
    """对 promoted 笔记做关键词检索。

    - query 按空白分词，全部 token 命中（AND，大小写不敏感）才收录；
      匹配域 = 标题 + 正文全文。
    - 排序：命中次数降序，同分按路径字典序。
    - limit 缺省或 <=0：返回全部命中（图谱跟读不得按条数截断）。
      用户显式给正整数时才截断。
    - 只读：不写任何文件；样本集 exact 限定 wiki/sources/ 内 promoted 笔记。
    """
    return scan_report(root, query, limit=limit).results


@dataclass(frozen=True)
class ScanReport:
    """一次扫描的可见失败面（constitution P4 部分失败显式化）。

    skipped 是 (vault 相对路径, SkipReason) 列表：单条不可解析 / 超预算 /
    非 UTF-8 时跳过该条并在此计数，其余可解析笔记正常返回——绝不整批
    静默吞掉，也不让单条坏形状炸掉整次检索。
    """

    results: list[SearchResult]
    skipped: list[tuple[str, SkipReason]]


def scan_report(root: Path, query: str, limit: int | None = None) -> ScanReport:
    """search 的报告形态：结果 + 被跳过笔记的显式清单（sc-engine-input-shapes）。

    不可信读路径四种形状的处置：
      - frontmatter 缺失/畸形 → 跳过计数（无 frontmatter 不可能是 promoted）
      - 超大文件（> MAX_NOTE_BYTES）→ 跳过计数，不整读
      - 非 UTF-8 字节 → 跳过计数（read_text 的 UnicodeDecodeError 路径）
      - OSError（权限等）→ 跳过计数
    """
    tokens = [t.lower() for t in query.split()]
    if not tokens:
        return ScanReport(results=[], skipped=[])
    sources_dir = root / SOURCES_SUBDIR
    if not sources_dir.is_dir():
        return ScanReport(results=[], skipped=[])

    results: list[SearchResult] = []
    skipped: list[tuple[str, SkipReason]] = []
    for note_path in sorted(sources_dir.rglob("*.md")):
        if note_path.name == _INDEX_FILENAME:
            continue
        rel_path = note_path.relative_to(root).as_posix()
        try:
            if note_path.stat().st_size > MAX_NOTE_BYTES:
                skipped.append((rel_path, SKIP_OVERSIZE))
                continue
            text = note_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append((rel_path, SKIP_NON_UTF8))
            continue
        except OSError:
            skipped.append((rel_path, SKIP_FRONTMATTER))
            continue
        fm_text, body = _split_frontmatter(text)
        if not fm_text:
            # frontmatter 缺失或 --- 围栏畸形：不可能满足 promoted 四要素
            skipped.append((rel_path, SKIP_FRONTMATTER))
            continue
        if not is_promoted(fm_text):
            continue
        title = _extract_title(fm_text, note_path.stem)
        haystack = f"{title}\n{body}".lower()
        if not all(t in haystack for t in tokens):
            continue
        score = sum(haystack.count(t) for t in tokens)
        how_text = extract_how_section(body)
        related, links = extract_graph_fields(fm_text, body)
        results.append(
            SearchResult(
                path=rel_path,
                title=title,
                how_density=how_text,
                how_executability=extract_executable_commands(how_text),
                score=score,
                related_concepts=related,
                graph_links=links,
            )
        )
    results.sort(key=lambda r: (-r.score, r.path))
    if limit is not None and limit > 0:
        results = results[:limit]
    return ScanReport(results=results, skipped=skipped)
