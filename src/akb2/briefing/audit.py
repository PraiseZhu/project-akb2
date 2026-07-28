"""audit.py — 译后自检阶段：审标准（代码）+ 审漂移/准确（LLM）。

返回结构化 verdict：
  {
    "standard": "pass" | "fail",
    "standard_issues": [...],        # 代码检查发现的具体问题
    "llm_audit": "pass" | "warn" | "fail" | "skipped",
    "drift_score": 0.0-1.0,          # 0=无漂移, 1=严重漂移（LLM 不可用时为 null）
    "accuracy_issues": [...],         # LLM 找出的数字/工具名丢失或译错
    "verdict": "pass" | "warn" | "fail",
    "ts": "<ISO8601>",
  }

verdict 聚合规则（enforcement matrix，2026-07-26 起 LLM 判 fail 不再降级）：
  - standard=fail        → verdict=fail（不入库）
  - llm_audit=fail       → verdict=fail（不入库）
  - llm_audit=skipped    → verdict=warn + audit_degraded=True（入库但强制降级标记）
  - standard 仅有 warn 级确定性问题 → verdict=warn
  - 其余                  → verdict=pass

pipeline 侧消费契约：fail 不发布；warn 发布但强制降级标记。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime

# 审计专用模型（默认中文母语模型）
_AUDIT_MODEL = os.environ.get("AKB2_AUDIT_MODEL", "qwen/qwen3.6-plus")

_AUDIT_SYSTEM = (
    "你是严格的 AI 趋势简报质量审计员。\n"
    "对比英文原版和中文译版，检查：\n"
    "1. 数字是否准确（GitHub stars、百分比、版本号等）\n"
    "2. 工具名/模型名是否保留或正确处理（如 LangGraph → LangGraph，不应被翻译）\n"
    "3. 主要事实是否漂移（无中生有、意义反转、重要事实删减）\n"
    "输出 JSON，不添加额外说明。"
)


# ---------------------------------------------------------------------------
# 代码标准检查（不依赖 LLM）
# ---------------------------------------------------------------------------

def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def _requires_chinese(text: str) -> bool:
    """分析字段（非空时）必须含中文，否则视为未翻译。"""
    if not text or len(text.strip()) == 0:
        return False  # 空字段不判
    return not _has_chinese(text)


_MIN_SUMMARY_SENTENCES = 3


_MIN_COMMA_CLAUSE_LEN = 50   # 逗号连缀判达标的最小字数
_MIN_COMMA_CLAUSES = 3       # 逗号分段数达到此值视为"≥3 句"


def _count_sentences(text: str) -> int:
    """计算句数，兼容两种写法：

    1. 句末标点分句（。？！；…）—— 标准写法
    2. 逗号连缀长句 —— DeepSeek 实际产出；当标点分句 < _MIN_SUMMARY_SENTENCES 且
       文本够长（≥_MIN_COMMA_CLAUSE_LEN 字）且逗号分段数 ≥_MIN_COMMA_CLAUSES 时，
       返回逗号分段数，避免误判"句数不足"
    """
    if not text or not text.strip():
        return 0
    stripped = text.strip()
    # 按句末标点切分
    parts = re.split(r'[。？！；…]+|(?<=[.!?])\s', stripped)
    punct_count = len([p for p in parts if p.strip()])
    if punct_count >= _MIN_SUMMARY_SENTENCES:
        return punct_count
    # 兜底：逗号连缀长句
    if len(stripped) >= _MIN_COMMA_CLAUSE_LEN:
        comma_parts = [c for c in re.split(r'[，,]+', stripped) if c.strip()]
        if len(comma_parts) >= _MIN_COMMA_CLAUSES:
            return len(comma_parts)
    return punct_count


def _get_referenced_tweet_ids(synth: dict) -> set[str]:
    """返回被任何主题 supporting_tweet_ids 引用的推文 id 集合。"""
    ids: set[str] = set()
    for fd in synth.get("focus_discussions", []):
        for tid in fd.get("supporting_tweet_ids", []):
            ids.add(str(tid))
    return ids


# ---------------------------------------------------------------------------
# 确定性译后校验（#13）：不调 LLM，纯代码比对英文原文与中文译文
#
# 背景：此前"是否已翻译"的唯一判据是 _has_chinese（有一个汉字就算已译），所以
# 「405B → 4050 亿参数」「72% on SWE-bench → 72% 准确率」这类事实损坏零拦截。
#
# 数字判据分两层（关键：区分"合法本地化"与"标识符被换算"）：
#   第 1 层 标识符类 —— 必须逐字保留：型号参数规模（紧贴数字的大写 B：405B/8B）、
#           版本号（v0.22.0）、千分位长数（18,432）。这类 token 是名字不是数量，
#           哪怕换算后数值相等（4050 亿 == 405B）也算损坏。
#   第 2 层 量词类 —— 允许合法本地化，按数值等价判定：50k→5万(5e4)、2M→200万(2e6)、
#           72%→72%。只校验"显著数字"（带量级/百分号/小数/千分位/≥3 位），
#           裸 1-2 位小整数跳过（"2 things→两件事"这类是假阳性主要来源，且非事实关键）。
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}'\"，。、；：！？（）《》]+")
_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,15}")

# 型号标识：紧贴数字的大写 B（405B / 8B / 1.5B）。AI 内容里 <数字>B 压倒性地指
# 参数规模（Llama 405B 是型号 SKU），而 prompt 规则 3 本就要求数字原样保留，
# 故要求逐字保留是既有契约的严格超集；货币/融资语境例外（$1B/raised 2B 是数量）。
_PARAM_SCALE_RE = re.compile(r"(?<![\w$¥€£.])(\d+(?:\.\d+)?B)(?![A-Za-z])")
_MONEY_CONTEXT_RE = re.compile(
    r"[$¥€£]|valuation|funding|revenue|raised|market cap|ARR", re.IGNORECASE
)
_VERSION_RE = re.compile(r"v\d+(?:\.\d+)+|\b\d+\.\d+\.\d+\b")
_THOUSANDS_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
_EN_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|[kKmMbBtT](?![A-Za-z]))?")
_ZH_NUM_RE = re.compile(
    r"(\d+(?:[.,]\d+)*)\s*(万亿|百万|十亿|亿|万|千|%|[kKmMbBtT](?![A-Za-z]))?"
)

_SCALE_MAP: dict[str, float] = {
    "": 1.0, "%": 1.0,
    "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "b": 1e9, "B": 1e9, "t": 1e12, "T": 1e12,
    "千": 1e3, "万": 1e4, "百万": 1e6, "十亿": 1e9, "亿": 1e8, "万亿": 1e12,
}

# 译文凭空指定指标类型 → 事实损坏。key=中文指标名，value=英文原文里必须出现的词根之一
#
# 严格度分两档（实测取舍）：
#   具名指标（准确率/正确率/召回率）—— 只认对应词根。SWE-bench 报的是 resolve rate，
#     原文没有 accuracy 词根却译成「准确率」就是事实错误（06-27 实际发生）。
#   速率类（解决率/通过率/成功率）—— 额外接受 rate / %。这类中文名之间语义邻接，
#     原文只要有速率/百分比概念，选哪个中文名是措辞而非捏造；实测 28/59 份日报出现
#     中文指标名，若一律严格判会把大量正常日报打成 fail。
_METRIC_ZH_EVIDENCE: dict[str, tuple[str, ...]] = {
    "准确率": ("accuracy", "accurate", "correct", "precision"),
    "正确率": ("accuracy", "correct"),
    "召回率": ("recall",),
    "解决率": ("resolve", "resolution", "solved", "solving", "fix", "rate", "%"),
    "通过率": ("pass", "passing", "passed", "rate", "%"),
    "成功率": ("success", "succeed", "rate", "%"),
}

# 语言纯度：英文小写虚词计数。技术栈堆叠（Remix / React / TypeScript / Prisma）与
# 保留的项目原名（Machine Learning for Algorithmic Trading）几乎不含小写虚词，
# 而"整句英文没译"必然带一串小写虚词 —— 用虚词数而非"英文长词数"做判据，
# 把实测 98.6% 的假阳性压下去。
_EN_STOPWORDS = frozenset({
    "the", "of", "and", "is", "are", "to", "with", "that", "this", "for", "on", "in",
    "a", "an", "as", "be", "been", "was", "were", "it", "its", "they", "their", "we",
    "our", "you", "your", "not", "but", "from", "have", "has", "had", "can", "will",
    "would", "should", "could", "by", "at", "if", "or", "which", "what", "how", "when",
    "there", "these", "those", "about", "all", "more", "than", "so", "just", "like",
    "do", "does", "did", "into", "out", "up", "over", "also", "because",
})
_MAX_EN_STOPWORDS = 2          # 小写虚词数上限
_MIN_EN_PROSE_RATIO = 0.35     # 且英文占比须过半线，避免"中文句中引用一段英文原话"被误判
_MIN_PROSE_HAN = 6             # 分析类长文本的汉字下限
_PROSE_HAN_MIN_LEN = 80        # 实测：术语密集短行（"NVFP4 评估：MMLU Pro 86.3"）汉字天然少，
                               # 而生产里的 executive_summary / summary 都在 100 字以上


def _strip_urls_handles(text: str) -> str:
    """把 URL 与 @handle 换成空格（它们各有专门检查，避免数字/虚词重复计入）。"""
    return _HANDLE_RE.sub(" ", _URL_RE.sub(" ", text or ""))


def _num_value(digits: str, suffix: str) -> float | None:
    try:
        return float(digits.replace(",", "")) * _SCALE_MAP.get(suffix or "", 1.0)
    except ValueError:
        return None


def _is_salient_number(digits: str, suffix: str) -> bool:
    """是否为需要校验的"显著数字"（见本节 docstring 第 2 层）。"""
    if suffix:
        return True
    if "," in digits or "." in digits:
        return True
    return len(digits) >= 3


def _zh_number_values(zh: str) -> list[float]:
    values: list[float] = []
    for m in _ZH_NUM_RE.finditer(zh or ""):
        v = _num_value(m.group(1), m.group(2) or "")
        if v is not None:
            values.append(v)
    return values


def _values_equivalent(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, abs(a) * 1e-9)


def _check_numbers_detailed(en: str, zh: str) -> list[tuple[str, str]]:
    """返回 [(问题描述, severity)]。severity ∈ {fail, warn}。

    校准口径（**先读这段再读下面的数字**）：

      - 可测 = **曝光面**：判据在已发布产出里多久开火一次、有多少 token 可被它扫到。
      - **不可测 = 误报率**：开火里有多少是错的。所有 fail 级判据都要「英文原文 ↔
        中文译文」配对，vault 里只有中文成品 → 曝光面能算，误报率不能。
      - 恒等回放（en := 已发布 ZH 文本自身）**尤其不能**用来论证"无结构性假阳性"：
        en == zh 时每个数字 token 必然逐字保留、每个 URL/@handle 必然存在，数字 /
        URL / handle 三类检查在构造上不可能报错，任何"0 误报""N/N 逐字保留"都是
        同义反复而非证据。它唯一有效的用途是暴露"en 侧其实不是英文"这类前提破坏
        （见 `_check_metric_fabrication`）。
      - 要真的量化误报率：靠 `translate.dump_fidelity_pairs` 累积的真机 en/zh 配对
        语料 + 人工裁决，出数通道是 `evals/audit-fidelity-fp/run.py`（语料/裁决不够
        时它输出"样本不足"，不给假数字）。

    可复现命令（下面所有数字都出自它，2026-07-28 跑过；vault 只读）::

        python3 evals/audit-fidelity-fp/run.py --corpus-mode identity \\
            --vault-root /Volumes/AKB2/Obsidian --report-out /tmp/exposure.json

    该命令 2026-07-28 的输出（60 篇已发布简报 / 59 天 / 1587 个受检配对）：

      - 4 个 fail 级 kind 触发数全 0（`exposure.per_kind`）—— 这只说明"恒等回放不
        会让它们开火"，**不是**误报率，见上。
      - `exposure.token_families`：`param_scale` 122 token（去重 22）分布在 33 天；
        `thousands` 4 token / 3 天；`version` 25 token / 12 天；`url` 1 token / 1 天；
        `handle` 107 token（去重 64）/ 44 天。
        → 千分位与版本号两类"必须逐字保留"的判据，其可扫 token 在现网产出上很稀
          （4 与 25 个），即**曝光面低**，故保留 fail 级的可用性代价可控。
          注意"保留率"在恒等回放下恒为 100%，不构成任何证据。
      - 注意该语料是**下界**：`load_identity_corpus` 只还原确定性 markdown 结构
        （执行摘要 / `### 主题` / `**@handle**` + `>` 引用），列表与表格里的文本不进
        配对，故真实曝光面 ≥ 上列数字。

    参数规模 B 的 warn 降级依据（**未保留可复现命令，本轮未复现**）：
        2026-07-26 一次性人工回放报告"9/60 篇已发布简报把 400B 本地化成
        「4000 亿参数」且数值等价"，据此把这一类从 fail 降为 warn
        （kind=number_localized）。降级方向是保守的（阻断 → 告警），故即使该数字偏
        差也不会造成漏发以外的风险；但"本地化占比"本身需要 en/zh 配对才能重算，
        恒等回放（en == zh，token 必然逐字保留）**测不到它**。待配对语料攒够后由
        `evals/audit-fidelity-fp` 复核，不要把 9/60 当已验证事实引用。
    """
    problems: list[tuple[str, str]] = []
    en_body = _strip_urls_handles(en)
    zh_body = _strip_urls_handles(zh)
    zh_lower = zh_body.lower()
    zh_digits = zh_body.replace(",", "").replace(" ", "")
    zh_values = _zh_number_values(zh_body)

    def _preserved(token: str) -> bool:
        plain = token.replace(",", "")
        return token.lower() in zh_lower or plain.lower() in zh_digits.lower()

    # 第 1 层a：参数规模标识（405B/8B）——逐字保留优先，数值等价本地化降 warn
    param_tokens: list[str] = []
    for m in _PARAM_SCALE_RE.finditer(en_body):
        window = en_body[max(0, m.start() - 24): m.end() + 24]
        if _MONEY_CONTEXT_RE.search(window):
            continue  # $1B / raised 2B：是数量，可本地化
        param_tokens.append(m.group(1))
    for token in dict.fromkeys(param_tokens):
        if _preserved(token):
            continue
        v = _num_value(token[:-1], token[-1])
        if v is not None and any(_values_equivalent(v, zv) for zv in zh_values):
            problems.append(
                (f"{token}（型号标识被本地化为等值中文量级，建议逐字保留）", "warn")
            )
            continue
        problems.append((f"{token}（标识符类，必须逐字保留）", "fail"))

    # 第 1 层b：版本号 / 千分位长数 —— 必须逐字保留。曝光面低（见 docstring 的
    # token_families：thousands 4 token / version 25 token），故 fail 级代价可控；
    # 误报率仍未测（恒等回放测不了，需配对语料 + 人工裁决）。
    strict_ids: list[str] = [m.group(0) for m in _VERSION_RE.finditer(en_body)]
    strict_ids += [m.group(0) for m in _THOUSANDS_RE.finditer(en_body)]
    for token in dict.fromkeys(strict_ids):
        if _preserved(token):
            continue
        problems.append((f"{token}（标识符类，必须逐字保留）", "fail"))

    # 第 2 层：显著数字按数值等价判定
    for m in _EN_NUM_RE.finditer(en_body):
        digits, suffix = m.group(1), m.group(2) or ""
        if not _is_salient_number(digits, suffix):
            continue
        raw = digits.replace(",", "")
        if raw in zh_digits:
            continue
        v = _num_value(digits, suffix)
        if v is not None and any(_values_equivalent(v, zv) for zv in zh_values):
            continue
        token = m.group(0).strip()
        if any(token in p for p, _ in problems):
            continue
        problems.append((f"{token}（译文中无对应数值）", "fail"))
    return problems


def _check_numbers(en: str, zh: str) -> list[str]:
    """向后兼容包装：返回全部数字问题描述（不分级）。"""
    return [msg for msg, _ in _check_numbers_detailed(en, zh)]


def _check_verbatim(en: str, zh: str, pattern: re.Pattern[str]) -> list[str]:
    """URL / @handle 逐字存在校验，返回缺失 token 列表。"""
    missing: list[str] = []
    for token in dict.fromkeys(m.group(0) for m in pattern.finditer(en or "")):
        if token not in (zh or ""):
            missing.append(token)
    return missing


def _is_cjk_source(en: str) -> bool:
    """"英文原文"侧其实是中日韩散文吗？

    判据 = **汉字数 ≥ `_MIN_PROSE_HAN`** 且 **英文虚词数 ≤ `_MAX_EN_STOPWORDS`**，
    即"有成段 CJK 正文，且没有英文散文的痕迹"。两个条件都是正向证据，缺一不可：
      - 只看汉字数：真英文原文里夹一个中文产品名就会被误判成中文。
      - 只看虚词数：短英文标题（"SWE-bench results"）天然没有虚词，会被误判成中文，
        白白放过真实捏造。
    虚词（而非"英文长词数"）作为英文散文判据是本模块已有的既有校准
    （见 `_EN_STOPWORDS` 上方注释：技术栈堆叠/项目原名几乎不含小写虚词），这里复用
    同一判据而不另造阈值。

    实测（可复现，2026-07-28；恒等回放 60 篇已发布简报 = 1587 个受检配对，en 侧全为
    中文）：本判据判 CJK 源 1513 / 1587，其余 74 条不判（多为汉字 <`_MIN_PROSE_HAN`
    的短标题）。开/关本门对比 `metric_fabricated` 触发数 = 31 → 0，即 31 条构造性
    命中被全部抑制::

        R="python3 evals/audit-fidelity-fp/run.py --corpus-mode identity \\
            --vault-root /Volumes/AKB2/Obsidian"
        eval "$R"                              # 门开启：metric_fabricated 触发 0
        eval "$R --replay-without-cjk-gate"    # 门关闭（还原加门前实现）：触发 31 / 19 天

    这 31 条是**构造性**假阳（en 侧是中文，英文词根在构造上不可能出现），不是对真实
    en/zh 配对测出的误报率 —— 后者仍需配对语料，见 `_check_numbers_detailed` docstring。
    """
    body = _strip_urls_handles(en or "")
    han = len(re.findall(r"[一-鿿]", body))
    if han < _MIN_PROSE_HAN:
        return False
    stop_hits = [w for w in re.findall(r"\b[a-z]+\b", body) if w in _EN_STOPWORDS]
    return len(stop_hits) <= _MAX_EN_STOPWORDS


def _check_metric_fabrication(en: str, zh: str) -> list[str]:
    """译文出现英文原文没有依据的指标类型（SWE-bench 是 resolve rate，不是准确率）。

    本判据是一个**缺席检验**：「en 里找不到 accuracy 词根」→ 判「准确率」是捏造。
    缺席检验只在"存在是可能的"时才携带信息。当 en 侧本身就是中文（中文源推文的
    `text_original` 兜底、或译前 synth_en 里混入中文原文），英文词根**在构造上**不可能
    出现 → 每个中文指标名都会 100% 命中，而且是 fail 级 → 当天日报直接不发布。
    这不是理论风险：恒等回放 60 篇已发布简报、关掉本门后得 31 条命中（19/59 天），全部
    是这个模式（复现命令见 `_is_cjk_source` docstring）。故中文↔中文的比较不走严格档，
    直接跳过本检查（与 `synth_en` 整体缺失时跳过所有需要英文原文的检查同构，见
    `_check_standard_structured` 文档）。

    这里只关掉本检查，不关数字/URL/@handle —— 那三类判据比对的是数字与逐字 token，
    与原文是什么文字无关，en 侧是中文时依然成立。
    """
    if _is_cjk_source(en):
        return []
    en_lower = (en or "").lower()
    fabricated: list[str] = []
    for zh_metric, evidence in _METRIC_ZH_EVIDENCE.items():
        if zh_metric in (zh or "") and not any(ev in en_lower for ev in evidence):
            fabricated.append(zh_metric)
    return fabricated


def _check_glossary(zh: str, en: str | None) -> list[str]:
    """术语表遵守校验（#14）。返回违规描述列表（warn 级）。"""
    from akb2.briefing.translate import KEEP_EN, TRANSLATION_GLOSSARY

    violations: list[str] = []
    zh_text = zh or ""
    zh_lower = zh_text.lower()
    for term, spec in TRANSLATION_GLOSSARY.items():
        # 禁用译法只看译文，无需英文原文即可判；同一术语只报第一个命中，避免
        # 「安德烈·卡帕斯」同时命中「卡帕斯」造成重复噪声
        hit_forbidden = next(
            (bad for bad in (spec.get("forbidden") or []) if bad in zh_text), None
        )
        if hit_forbidden:
            target = (
                f"保留英文「{term}」" if spec.get("zh") == KEEP_EN
                else f"「{spec.get('zh')}」"
            )
            violations.append(f"{term}: 使用了禁用译法「{hit_forbidden}」，应为{target}")
            continue  # 已报错译，不再重复报"未使用指定译法"
        if not en or term.lower() not in en.lower():
            continue
        expected = spec.get("zh")
        needle = term if expected == KEEP_EN else expected
        if needle and needle.lower() not in zh_lower:
            violations.append(
                f"{term}: 英文原文出现该术语，但译文未使用指定译法"
                f"{'（保留英文）' if expected == KEEP_EN else f'「{expected}」'}"
            )
    return violations


def _check_language_purity(zh: str, *, prose: bool) -> list[str]:
    """语言纯度（warn 级）：治中英混杂，同时避免技术栈堆叠/项目原名的假阳性。"""
    problems: list[str] = []
    body = _strip_urls_handles(zh or "")
    words = re.findall(r"\b[a-z]+\b", body)  # 只数小写词：技术栈/专名多为首字母大写
    stop_hits = [w for w in words if w in _EN_STOPWORDS]
    han = len(re.findall(r"[一-鿿]", body))
    ascii_letters = len(re.findall(r"[A-Za-z]", body))
    en_ratio = ascii_letters / (han + ascii_letters) if (han + ascii_letters) else 0.0
    if len(stop_hits) > _MAX_EN_STOPWORDS and en_ratio > _MIN_EN_PROSE_RATIO:
        problems.append(
            f"英文散文泄漏：命中 {len(stop_hits)} 个英文虚词 {sorted(set(stop_hits))[:6]}"
            f"（上限 {_MAX_EN_STOPWORDS}），英文字符占比 {en_ratio:.0%}"
            f"（上限 {_MIN_EN_PROSE_RATIO:.0%}）"
        )
    if prose and len(body.strip()) >= _PROSE_HAN_MIN_LEN:
        if han < _MIN_PROSE_HAN:
            problems.append(f"汉字过少：{han} 字 < 下限 {_MIN_PROSE_HAN}")
    return problems


def _translation_pairs(synth_zh: dict, synth_en: dict | None) -> list[dict]:
    """构造 (field, en, zh) 对照集合。synth_en 缺失时只带 zh（仅跑 ZH-only 检查）。"""
    pairs: list[dict] = []
    en = synth_en or {}

    pairs.append({
        "field": "executive_summary", "idx": None, "prose": True,
        "en": (en.get("executive_summary") or "").strip(),
        "zh": (synth_zh.get("executive_summary") or "").strip(),
    })

    en_fds = en.get("focus_discussions", []) or []
    for i, fd in enumerate(synth_zh.get("focus_discussions", [])):
        en_fd = en_fds[i] if i < len(en_fds) else {}
        pairs.append({
            "field": f"focus_discussions[{i}].summary", "idx": i, "prose": True,
            "en": (en_fd.get("summary") or "").strip(),
            "zh": (fd.get("summary") or "").strip(),
        })
        pairs.append({
            "field": f"focus_discussions[{i}].topic", "idx": i, "prose": False,
            "en": (en_fd.get("topic") or "").strip(),
            "zh": (fd.get("topic") or "").strip(),
        })

    from akb2.briefing.translate import _display_tweet_indices  # noqa: PLC0415

    en_by_id = {str(t.get("id", "")): t for t in en.get("tweets", []) or []}
    display_idxs = _display_tweet_indices(synth_zh)
    tweets = synth_zh.get("tweets", [])
    for i in sorted(display_idxs):
        if i >= len(tweets):
            continue
        tw = tweets[i]
        if (tw.get("lang") or "").lower() == "zh":
            continue  # 中文原文推文不参与译前/译后比对
        en_tw = en_by_id.get(str(tw.get("id", "")), {})
        en_text = (en_tw.get("text") or tw.get("text_original") or "").strip()
        pairs.append({
            "field": f"tweets[{i}].text", "idx": i, "prose": False,
            "en": en_text,
            "zh": (tw.get("text") or "").strip(),
        })
    return pairs


def _check_translation_fidelity(synth_zh: dict, synth_en: dict | None) -> list[dict]:
    """确定性译后校验，返回 issue 列表（含 severity）。"""
    issues: list[dict] = []

    def _add(kind: str, field: str, idx, msg: str, severity: str) -> None:
        issues.append({
            "kind": kind, "field": field, "idx": idx, "msg": msg, "severity": severity,
        })

    for pair in _translation_pairs(synth_zh, synth_en):
        field, idx, zh, en = pair["field"], pair["idx"], pair["zh"], pair["en"]
        if not zh:
            continue

        if en:
            num_problems = _check_numbers_detailed(en, zh)
            fail_nums = [msg for msg, sev in num_problems if sev == "fail"]
            warn_nums = [msg for msg, sev in num_problems if sev == "warn"]
            if fail_nums:
                _add("number_mismatch", field, idx,
                     f"{field} 数字与英文原文不一致: {'; '.join(fail_nums)}", "fail")
            if warn_nums:
                _add("number_localized", field, idx,
                     f"{field} 型号标识被本地化（数值等价）: {'; '.join(warn_nums)}", "warn")

            missing_urls = _check_verbatim(en, zh, _URL_RE)
            if missing_urls:
                # 严重度按字段分：推文正文的 URL 在渲染时会被
                # `pipeline._strip_inline_links` 主动删掉（用户决策：每条推文只留底部
                # 唯一 [原文] 链接），所以译文丢 URL 对**已发布产出没有任何影响**——
                # 拿 fail 级去守一个下游必然丢弃的属性，只会白白停发日报。
                # 分析字段（executive_summary / topic / summary）不经该剥离，URL 会
                # 逐字进产出，仍判 fail。曝光面（可复现，见 _check_numbers_detailed
                # docstring 的命令 → exposure.token_families.url）：60 篇已发布简报的
                # 1587 个受检配对里只有 1 个 URL token / 1 天，即该判据极少开火。
                severity = "warn" if field.startswith("tweets[") else "fail"
                suffix = (
                    "（推文正文 URL 渲染时本就被 _strip_inline_links 剥离，故仅告警）"
                    if severity == "warn" else ""
                )
                _add("url_missing", field, idx,
                     f"{field} 译文丢失 URL: {', '.join(missing_urls)}{suffix}", severity)

            missing_handles = _check_verbatim(en, zh, _HANDLE_RE)
            if missing_handles:
                _add("handle_missing", field, idx,
                     f"{field} 译文丢失 @handle: {', '.join(missing_handles)}", "fail")

            fabricated = _check_metric_fabrication(en, zh)
            if fabricated:
                _add("metric_fabricated", field, idx,
                     f"{field} 译文凭空指定指标类型 {fabricated}（英文原文无对应词根）",
                     "fail")

        for violation in _check_glossary(zh, en or None):
            _add("glossary_violation", field, idx, f"{field} 术语表违规: {violation}", "warn")

        for problem in _check_language_purity(zh, prose=pair["prose"]):
            _add("language_mixed", field, idx, f"{field} {problem}", "warn")

    return issues


def _check_standard_structured(
    synth_zh: dict,
    synth_en: dict | None = None,
) -> list[dict]:
    """代码规则检查，返回结构化 issues 列表。

    每条 issue: {"kind": str, "field": str, "idx": int|None, "msg": str, "severity": str}
    kind ∈ {untranslated, zh_point_missing, zh_point_untranslated, empty, structural,
            number_mismatch, number_localized, url_missing, handle_missing,
            metric_fabricated, glossary_violation, language_mixed}
    severity ∈ {fail, warn}；warn 级不阻断入库，但会把 verdict 压到 warn。

    fail 级确定性检查的可用性契约（校准口径与复现命令见 _check_numbers_detailed
    docstring）：任何 fail 级 kind 都**必须**同时出现在 `audit_fix._FIXABLE_KINDS`
    里。理由是量化的：60 篇已发布简报共 1587 个受检配对 = 平均 26.5 个/篇，单字段 1%
    的误报率就意味着 1-(1-0.01)^26.5 ≈ 23.3% 的天数被判 fail；没有修复通道的 fail 级
    检查 = 日报可用性单点。（26.5 是恒等回放语料的**下界**，见该 docstring 说明；这里
    的 1% 是敏感度演算的假设值，不是实测误报率——实测值当前仍未知。）

    synth_en 省略时跳过需要英文原文的确定性校验（数字/URL/@handle/指标），
    仅跑 ZH-only 检查（术语表禁用译法、语言纯度）。
    """
    items: list[dict] = []

    def _add(kind: str, field: str, idx, msg: str, severity: str = "fail") -> None:
        items.append({
            "kind": kind, "field": field, "idx": idx, "msg": msg, "severity": severity,
        })

    # executive_summary 必须非空且含中文
    summary = (synth_zh.get("executive_summary") or "").strip()
    if not summary:
        _add("empty", "executive_summary", None, "executive_summary 为空（必填字段）")
    elif _requires_chinese(summary):
        _add("untranslated", "executive_summary", None,
             f"executive_summary 无中文（未翻译）: {summary[:80]!r}")

    # 构建 tweet_id 集合用于支撑推文校验
    tweet_ids_in_payload = {str(t.get("id", "")) for t in synth_zh.get("tweets", [])}

    for i, fd in enumerate(synth_zh.get("focus_discussions", [])):
        topic = (fd.get("topic") or "").strip()
        sm = (fd.get("summary") or "").strip()
        if topic and not _has_chinese(topic):
            _add("untranslated", f"focus_discussions[{i}].topic", i,
                 f"focus_discussions[{i}].topic 无中文: {topic!r}")

        # SC-3: summary 必须非空且含中文，且 ≥3 句
        if not sm:
            _add("empty", f"focus_discussions[{i}].summary", i,
                 f"focus_discussions[{i}].summary 为空（必填字段）")
        elif _requires_chinese(sm):
            _add("untranslated", f"focus_discussions[{i}].summary", i,
                 f"focus_discussions[{i}].summary 无中文（未翻译）: {sm[:80]!r}")
        else:
            sent_count = _count_sentences(sm)
            if sent_count < _MIN_SUMMARY_SENTENCES:
                sm_msg = (
                    f"focus_discussions[{i}].summary 句数不足"
                    f"（{sent_count} 句 < {_MIN_SUMMARY_SENTENCES} 句）: {sm[:80]!r}"
                )
                _add("structural", f"focus_discussions[{i}].summary", i, sm_msg)

        # SC-1: supporting_tweet_ids 至少 2 个，且引用的推文必须存在于 tweets 列表
        sup_ids = fd.get("supporting_tweet_ids", [])
        if len(sup_ids) < 2:
            _add("structural", f"focus_discussions[{i}].supporting_tweet_ids", i,
                 f"focus_discussions[{i}].supporting_tweet_ids 少于 2 个（当前 {len(sup_ids)} 个）")
        for tid in sup_ids:
            if str(tid) not in tweet_ids_in_payload:
                ref_msg = (
                    f"focus_discussions[{i}].supporting_tweet_ids"
                    f" 引用了不存在的 tweet_id: {tid!r}"
                )
                _add("structural", f"focus_discussions[{i}].supporting_tweet_ids", i, ref_msg)

    # topic 非占位符
    for i, fd in enumerate(synth_zh.get("focus_discussions", [])):
        topic = (fd.get("topic") or "").strip()
        if re.match(r"^AI\s*趋势观察\s*#\d+$", topic):
            _add("structural", f"focus_discussions[{i}].topic", i,
                 f"focus_discussions[{i}].topic 仍是占位符: {topic!r}")

    # tweets text 与 core_excerpt：仅检查展示集合（与 translate 对齐，非展示推文无翻译）
    from akb2.briefing.translate import _ORPHAN_TOP_K, _display_tweet_indices
    display_idxs = _display_tweet_indices(synth_zh, _ORPHAN_TOP_K)
    for i, tw in enumerate(synth_zh.get("tweets", [])):
        if i not in display_idxs:
            continue  # 非展示推文不参与中文检查（未被翻译）
        text = (tw.get("text") or "").strip()
        if _requires_chinese(text):
            _add("untranslated", f"tweets[{i}].text", i,
                 f"tweets[{i}].text 无中文（未翻译）: {text[:60]!r}")
        core = (tw.get("core_excerpt") or "").strip()
        if _requires_chinese(core):
            _add("untranslated", f"tweets[{i}].core_excerpt", i,
                 f"tweets[{i}].core_excerpt 无中文（未翻译）: {core[:60]!r}")

    # SC-1: top-3 高分推文至少要出现在某个主题的 supporting_tweet_ids 里
    # idx=None：rank= 是分数排名，不是数组下标，标 structural 防止误用 idx 作为数组索引
    referenced_ids = _get_referenced_tweet_ids(synth_zh)
    all_tweets_sorted = sorted(
        synth_zh.get("tweets", []),
        key=lambda t: t.get("score", 0),
        reverse=True,
    )
    for rank, tw in enumerate(all_tweets_sorted[:3], start=1):
        tid = str(tw.get("id", ""))
        if tid and tid not in referenced_ids:
            _add("structural", f"tweets[rank={rank}]", None,
                 f"tweets[rank={rank}] id={tid!r} score={tw.get('score',0):.3f}"
                 f" 未出现在任何主题的 supporting_tweet_ids（top-3 必须有主题支撑）")

    # SC-8: 所有展示推文（被引用 + 孤儿）的 zh_point 必须存在且含中文
    tweets_list = synth_zh.get("tweets", [])
    for i in display_idxs:
        if i >= len(tweets_list):
            continue
        tw = tweets_list[i]
        zh_point = (tw.get("zh_point") or "").strip()
        if not zh_point:
            _add("zh_point_missing", f"tweets[{i}].zh_point", i,
                 f"tweets[{i}].zh_point 缺失（展示推文必须有中文点题）")
        elif not _has_chinese(zh_point):
            _add("zh_point_untranslated", f"tweets[{i}].zh_point", i,
                 f"tweets[{i}].zh_point 无中文: {zh_point!r}")

    # 确定性译后校验（#13/#14）
    items.extend(_check_translation_fidelity(synth_zh, synth_en))

    return items


def _check_standard(synth_zh: dict, synth_en: dict | None = None) -> list[str]:
    """代码规则检查，返回 issues 列表（空 = pass）。向后兼容包装器。"""
    return [it["msg"] for it in _check_standard_structured(synth_zh, synth_en)]


# ---------------------------------------------------------------------------
# LLM 审计（漂移 + 准确性）
# ---------------------------------------------------------------------------

_AUDIT_TRUNCATE_CHARS = 600
_AUDIT_TRUNCATE_MARKER = "[节选，原文更长]"


def _truncate_with_marker(text: str, max_chars: int = _AUDIT_TRUNCATE_CHARS) -> str:
    """截断文本并在超长时追加标记，避免审计模型因自截自判导致假阳性。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + _AUDIT_TRUNCATE_MARKER


def _build_audit_prompt(synth_zh: dict, synth_en: dict) -> str:
    en_topics = [fd.get("topic", "") for fd in synth_en.get("focus_discussions", [])]
    zh_topics = [fd.get("topic", "") for fd in synth_zh.get("focus_discussions", [])]
    en_summary = synth_en.get("executive_summary", "")
    zh_summary = synth_zh.get("executive_summary", "")

    en_tweets_sample = [
        {"idx": i, "text": _truncate_with_marker(tw.get("text", ""))}
        for i, tw in enumerate(synth_en.get("tweets", [])[:5])
    ]
    zh_tweets_sample = [
        {"idx": i, "text": _truncate_with_marker(tw.get("text", ""))}
        for i, tw in enumerate(synth_zh.get("tweets", [])[:5])
    ]

    payload = {
        "en": {
            "executive_summary": en_summary,
            "topics": en_topics,
            "tweets_sample": en_tweets_sample,
        },
        "zh": {
            "executive_summary": zh_summary,
            "topics": zh_topics,
            "tweets_sample": zh_tweets_sample,
        },
    }

    return (
        "对比以下英文原版与中文译版，审查翻译质量。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "请输出 JSON：\n"
        "```json\n"
        "{\n"
        '  "drift_score": 0.0~1.0,  // 0=忠实无漂移, 1=严重漂移/意义反转\n'
        '  "accuracy_issues": [      // 数字/工具名丢失或译错的列表，无则空数组\n'
        '    "issue description"\n'
        "  ],\n"
        '  "llm_verdict": "pass" | "warn" | "fail"  // pass=高质量, warn=轻微问题, fail=严重漂移\n'
        "}\n"
        "```"
    )


def _run_llm_audit(synth_zh: dict, synth_en: dict, model: str) -> dict:
    from akb2.briefing.synthesis import _call_llm

    prompt = _build_audit_prompt(synth_zh, synth_en)
    messages = [
        {"role": "system", "content": _AUDIT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    raw = _call_llm(messages, model=model, json_mode=True, timeout=120)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def audit_briefing(
    synth_zh: dict,
    synth_en: dict,
    *,
    model: str | None = None,
) -> dict:
    """执行译后自检，返回 verdict dict。LLM 不可用时仅运行代码检查。"""
    ts = datetime.now(UTC).isoformat()
    used_model = model or _AUDIT_MODEL

    # 1. 代码标准检查（含确定性译后校验；warn 级不判 standard=fail）
    standard_issues_structured = _check_standard_structured(synth_zh, synth_en)
    standard_issues = [it["msg"] for it in standard_issues_structured]
    fail_issues = [
        it for it in standard_issues_structured if it.get("severity", "fail") == "fail"
    ]
    warn_issues = [
        it for it in standard_issues_structured if it.get("severity") == "warn"
    ]
    standard = "fail" if fail_issues else "pass"

    # 2. LLM 漂移/准确审计
    llm_audit = "skipped"
    drift_score: float | None = None
    accuracy_issues: list[str] = []

    try:
        llm_result = _run_llm_audit(synth_zh, synth_en, model=used_model)
        drift_score = float(llm_result.get("drift_score", 0.0))
        accuracy_issues = llm_result.get("accuracy_issues") or []
        raw_verdict = llm_result.get("llm_verdict", "warn")
        # 白名单校验：未知值降级为 warn（fail-closed）
        _VALID_LLM_VERDICTS = {"pass", "warn", "fail"}
        if raw_verdict not in _VALID_LLM_VERDICTS:
            sys.stderr.write(
                f"WARNING [audit]: unexpected llm_verdict={raw_verdict!r}, "
                "treating as 'warn' (fail-closed).\n"
            )
            llm_audit = "warn"
        else:
            llm_audit = raw_verdict
    except Exception as e:
        sys.stderr.write(
            f"WARNING [audit]: LLM audit failed ({e}), skipping LLM checks.\n"
        )
        llm_audit = "skipped"

    # 3. 聚合 verdict（enforcement matrix；LLM 判 fail 不再降级为 warn）
    #    audit_degraded：LLM 审计没跑成 → 只过了代码检查，裁决可信度下降，
    #    pipeline 侧据此强制降级标记（warn 发布但标记）。
    audit_degraded = llm_audit == "skipped"
    if standard == "fail":
        verdict = "fail"
    elif llm_audit == "fail":
        verdict = "fail"
    elif audit_degraded or warn_issues:
        verdict = "warn"
    else:
        verdict = "pass"

    result = {
        "standard": standard,
        "standard_issues": standard_issues,
        "standard_issues_structured": standard_issues_structured,
        "standard_warn_issues": [it["msg"] for it in warn_issues],
        "llm_audit": llm_audit,
        "audit_degraded": audit_degraded,
        "drift_score": drift_score,
        "accuracy_issues": accuracy_issues,
        "verdict": verdict,
        "ts": ts,
    }

    if not _log_audit(result):
        # 裁决记录写盘失败：不得静默丢失（constitution 原则 4）
        result["audit_log_failed"] = True
    return result


def _log_audit(verdict: dict) -> bool:
    """追加审计裁决到日志文件。返回是否写入成功（失败时 stderr 告警，不静默）。"""
    import json
    from pathlib import Path

    log_dir = Path(os.environ.get("AKB2_LOG_DIR", "/tmp/akb2-logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "audit.jsonl", "a") as f:
            f.write(json.dumps(verdict, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        sys.stderr.write(
            f"WARNING [audit]: failed to persist audit verdict to {log_dir}/audit.jsonl "
            f"({e}); verdict={verdict.get('verdict')!r} standard={verdict.get('standard')!r} "
            f"llm_audit={verdict.get('llm_audit')!r} (audit_log_failed=True).\n"
        )
        return False
