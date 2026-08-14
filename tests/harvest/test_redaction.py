"""sc-asm-redact 验收:落盘前凭证扫描与策略(Reject / Redact 两值)。

TDD 纪律:各模式族正例 fixture 先写成预测红集(断言对错误实现判别性),
实现后转绿;假阳性 fixture 直通;--redact 后全模式族 0 命中;高熵未知值
默认拒且 stderr 不含值本身。
"""

from __future__ import annotations

import pytest

from akb2.harvest.redaction import (
    REDACTED_MARKER,
    RedactionPolicy,
    RedactionRejectedError,
    scan_and_redact,
)

# ---------------------------------------------------------------------------
# fixture:各模式族正例(含换行/分隔变体)
# ---------------------------------------------------------------------------


def _payload() -> dict:
    return {
        "schema_version": "1",
        "session_id": "a1b2c3d4e5f6a7b8c9d0e1f2",
        "title": "t",
        "lane_hint": "procedural",
        "components": ["c"],
        "causal_edges": [],
        "atoms": [{"id": "a1", "kind": "command", "text": "x"}],
        "how_steps": [{"anchor": "a1", "provenance": "p", "cmd": "c",
                       "expect": "e"}],
        "source_meta": {},
    }


# (含换行/分隔变体的各模式族 markdown 样例)
PATTERN_FIXTURES: list[tuple[str, str]] = [
    # API key 赋值
    ("api_key_assignment",
     'export API_KEY="sk-abc1234567890abcdef"'),
    # Authorization 头 Bearer / Basic(带换行变体)
    ("authorization_bearer",
     'Authorization: Bearer e3b0c44298fc1c149afbf4c8996fb924'),
    ("authorization_basic",
     'auth = "Basic dXNlcjpwYXNz"'),
    # URL query / fragment 凭证参数
    ("url_query_token",
     "https://api.example.com/v1?access_token=e3b0c44298fc1c149afbf4c8996fb924"),
    ("url_query_client_secret",
     "https://x?a=1#client_secret=deadbeefdeadbeefdeadbeefdeadbeef"),
    # PEM 私钥块
    ("pem_private_key",
     "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"),
    # JWT 三段式
    ("jwt",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    # GitHub token(ghp_ / gho_ / github_pat_)
    ("github_ghp", "ghp_e3b0c44298fc1c149afbf4c8996fb924abcd"),
    ("github_gho", "gho_e3b0c44298fc1c149afbf4c8996fb924abcd"),
    ("github_pat", "github_pat_11AAAaaa222BBBbbb333CCCccc"),
    # AWS AKIA + secret 赋值
    ("aws_akia", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_key", 'AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'),
    # 连接串 user:password@(含 markdown 链接内)
    ("connstring",
     "postgresql://user:secret123@db.example.com:5432/mydb"),
    ("markdown_link_connstring",
     "[db](postgres://user:secret123@host/db)"),
    # env / JSON / YAML 键名赋值
    ("env_api_key", "API_KEY=abc123def456ghi789jkl012"),
    ("yaml_client_secret", "client_secret: xyzzy1234567890qwerty"),
    ("json_password", '"password": "hunter2hunter2hunter2"'),
    ("private_key_value", "private_key = deadbeefcafebabedeadbeefcafe"),
    # markdown URL fragment 内凭证
    ("md_fragment", "[link](https://x/y?token=e3b0c44298fc1c149afbf4c8996fb924)"),
]

# 假阳性 fixture:叙述性 token 字样 / 普通文本,必须直通
FALSE_POSITIVE_FIXTURES: list[str] = [
    "token budget 计算:本次节省 300k tokens",
    "token 节省策略:减少冗余输出",
    "password 一词在文档中用作名词,无赋值",
    "api key 概念介绍,无值",
    "the secret of success is hard work",
    "Bearer of the news arrived",
    "I have a token of appreciation",
    # 长度不足的高熵(不满足 ≥24)
    "KEY=shortvalue",
]


@pytest.fixture()
def markdown_samples() -> dict[str, str]:
    return {name: text for name, text in PATTERN_FIXTURES}


# ---------------------------------------------------------------------------
# 1. 各模式族正例:Reject 默认必命中(预测红集:对「只扫 payload 不扫 markdown」
#    的实现,「frontmatter/正文里出现的凭证扫不到」必红;对「无模式族实现」
#    的实现全族必红)
# ---------------------------------------------------------------------------


class TestPatternFamilies:
    @pytest.mark.parametrize("name,text", PATTERN_FIXTURES)
    def test_reject_hits(self, name: str, text: str) -> None:
        """该模式族形态在 Reject 下必命中(默认策略,命中即拒)。"""
        md = f"---\nartifact_type: raw\n---\n\n正文\n\n{text}\n"
        with pytest.raises(RedactionRejectedError) as ei:
            scan_and_redact(_payload(), md, policy=RedactionPolicy.Reject)
        # 不回显命中值(消息里不出现凭证正文)
        assert text.split("=", 1)[-1].split()[0] not in str(ei.value)

    @pytest.mark.parametrize("name,text", PATTERN_FIXTURES)
    def test_redact_replaces(self, name: str, text: str) -> None:
        """Redact 分支:命中被替换为 [REDACTED],且替换清单带位置。"""
        md = f"---\nartifact_type: raw\n---\n\n{text}\n"
        out, findings = scan_and_redact(_payload(), md,
                                        policy=RedactionPolicy.Redact)
        assert text not in out  # 凭证原文不再出现
        assert REDACTED_MARKER in out
        assert findings, "替换必须产出带位置的清单"

    def test_scan_objects_include_payload_and_markdown(self) -> None:
        """扫描对象 = 输入 JSON 全文 + 渲染后 markdown 全文。"""
        # payload 内凭证(如 source_meta 里的 access_token)
        p = _payload()
        p["source_meta"]["access_token"] = "e3b0c44298fc1c149afbf4c8996fb924"
        md = "---\nartifact_type: raw\n---\n\n正文(无凭证)\n"
        with pytest.raises(RedactionRejectedError):
            scan_and_redact(p, md, policy=RedactionPolicy.Reject)
        # markdown frontmatter 内凭证(archive_raw 生成的 9 字段里)
        p2 = _payload()
        md2 = ("---\nartifact_type: raw\nsource_url: "
               "https://x?token=e3b0c44298fc1c149afbf4c8996fb924\n---\n")
        with pytest.raises(RedactionRejectedError):
            scan_and_redact(p2, md2, policy=RedactionPolicy.Reject)


# ---------------------------------------------------------------------------
# 2. 假阳性 fixture 直通(叙述性 token 字样不误伤)
# ---------------------------------------------------------------------------


class TestFalsePositives:
    @pytest.mark.parametrize("text", FALSE_POSITIVE_FIXTURES)
    def test_clean_text_passes(self, text: str) -> None:
        md = f"---\nartifact_type: raw\n---\n\n{text}\n"
        out, findings = scan_and_redact(_payload(), md,
                                        policy=RedactionPolicy.Reject)
        assert out == md
        assert findings == []


# ---------------------------------------------------------------------------
# 3. --redact 后全模式族 0 命中 + 未知高熵默认拒
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_redact_after_all_families_zero_hits(self) -> None:
        """Redact 后最终文本再扫一遍必须 0 命中(不留可再命中的残余)。"""
        md = "\n".join(text for _name, text in PATTERN_FIXTURES)
        out, _findings = scan_and_redact(_payload(), md,
                                         policy=RedactionPolicy.Redact)
        _, again = scan_and_redact(_payload(), out,
                                   policy=RedactionPolicy.Reject)
        assert again == []

    def test_unknown_high_entropy_rejected(self) -> None:
        """未知键名 + 高熵值默认拒;stderr 不含值本身。"""
        value = "x7Q2mN9vL4kP8rT3wZ6aB1cD5eF0gH2jY"  # 高熵 ≥24
        md = f"---\nartifact_type: raw\n---\n\nMYSTERY_KEY={value}\n"
        with pytest.raises(RedactionRejectedError) as ei:
            scan_and_redact(_payload(), md, policy=RedactionPolicy.Reject)
        assert value not in str(ei.value)

    def test_unknown_low_entropy_passes(self) -> None:
        """低熵未知值不拒(避免过度拦截)。"""
        md = "---\nartifact_type: raw\n---\n\nMYSTERY_KEY=abc123\n"
        out, findings = scan_and_redact(_payload(), md,
                                        policy=RedactionPolicy.Reject)
        assert out == md
        assert findings == []

    def test_policy_enum_exactly_two_values(self) -> None:
        """policy 取值域恰为 {Reject, Redact} 两值,无第三值。"""
        members = set(RedactionPolicy)
        assert members == {RedactionPolicy.Reject, RedactionPolicy.Redact}
        assert len(members) == 2

    def test_policy_type_enforced(self) -> None:
        """非 RedactionPolicy 值抛 TypeError(不静默当 Reject)。"""
        with pytest.raises(TypeError):
            scan_and_redact(_payload(), "md", policy=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. (p) typed policy 三例:assemble 层集成见 test_assembler.py,
#    此处验证 CLI 侧零预处理(由 test_assembler 断言 CLI 模块零写盘零替换)
# ---------------------------------------------------------------------------


class TestRedactionModuleSurface:
    def test_error_message_points_position_not_value(self) -> None:
        """拒绝消息点名位置与模式类,不回显命中值。"""
        md = ("---\nartifact_type: raw\n---\n\n"
              "Authorization: Bearer e3b0c44298fc1c149afbf4c8996fb924\n")
        with pytest.raises(RedactionRejectedError) as ei:
            scan_and_redact(_payload(), md, policy=RedactionPolicy.Reject)
        msg = str(ei.value)
        assert "line" in msg
        assert "authorization_header" in msg
        assert "e3b0c44298fc1c149afbf4c8996fb924" not in msg

    def test_replacements_do_not_corrupt_surrounding_text(self) -> None:
        """替换只动命中区间,周围字节逐字保留。"""
        prefix = "前置文本 αβγ\n"
        suffix = "\n后置文本 123"
        secret = "e3b0c44298fc1c149afbf4c8996fb924"
        md = prefix + "Bearer " + secret + suffix
        out, _f = scan_and_redact(_payload(), md,
                                  policy=RedactionPolicy.Redact)
        assert out.startswith(prefix)
        assert out.endswith(suffix)
        assert secret not in out
        assert REDACTED_MARKER in out

    def test_json_payload_scan_redact_ignored(self) -> None:
        """Redact 分支:payload 命中不产生替换动作(落盘的是 markdown)。"""
        p = _payload()
        p["source_meta"]["access_token"] = "e3b0c44298fc1c149afbf4c8996fb924"
        md = "---\nartifact_type: raw\n---\n\n正文\n"
        out, findings = scan_and_redact(p, md, policy=RedactionPolicy.Redact)
        assert out == md
        assert findings == []

    def test_markdown_and_payload_both_required_for_reject(self) -> None:
        """Reject 分支:markdown 或 payload 任一命中都拒,两者都要扫。"""
        md = "---\nartifact_type: raw\n---\n\n正文\n"
        # 仅 markdown 命中
        md_hit = md + "Bearer e3b0c44298fc1c149afbf4c8996fb924\n"
        with pytest.raises(RedactionRejectedError):
            scan_and_redact(_payload(), md_hit, policy=RedactionPolicy.Reject)
        # 仅 payload 命中
        p_hit = _payload()
        p_hit["atoms"][0]["text"] = \
            "Bearer e3b0c44298fc1c149afbf4c8996fb924"
        with pytest.raises(RedactionRejectedError):
            scan_and_redact(p_hit, md, policy=RedactionPolicy.Reject)
