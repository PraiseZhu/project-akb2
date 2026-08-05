"""SC-R7-06: 旧凭据名 `git grep` 守卫必须区分「无命中」与「工具没跑成」。

R6 里 `assert grep.returncode == 1` 把 rc=1（无命中）当作唯一通过条件，副作用是
任何其他非零 rc（例如在非 git 目录里跑出的 128）连带被判为失败——看起来是安全的，
但失败文案却写「still referenced in: <空清单>」，跟真实证据（压根没找到引用，因为
工具没跑成）自相矛盾。这里锁死三态：rc=0 真命中才失败、rc=1 才是无命中通过、
其余 rc 必须单独报「工具错误」，不得被伪装成阴性通过。
"""

from __future__ import annotations

import pytest

from tests.briefing.backstop_lock_test_support import (
    LegacyCredentialGrepResult,
    _assert_legacy_credential_grep_clean,
    _run_legacy_credential_grep,
)

# 本文件不得使用真实旧凭据名的字面量（LEGACY_HELD_ENV/LEGACY_FD_ENV 的完整拼接），
# 否则本文件被 git 追踪后自己就会变成 assert_no_forgeable_reentry_credential_exists
# 的 grep 命中来源。这里用一个与真实凭据名无关的占位名，两个测试都只关心
# `_assert_legacy_credential_grep_clean` 对 rc 的三态分支，name 本身只是回填进
# 失败文案，取什么值不影响被测行为。
_PLACEHOLDER_NAME = "some-probe-name-unrelated-to-real-credentials"


def test_tool_error_distinguished_from_no_match(tmp_path):
    """在真实的非 git 目录里跑 `git grep`，必须拿到工具错误 rc（如 128），
    且该守卫必须为此报错，不能悄悄当作「无命中」通过。
    """
    result = _run_legacy_credential_grep(_PLACEHOLDER_NAME, tmp_path)
    assert result.returncode not in (0, 1), (
        f"expected a git tool error outside a repo, got rc={result.returncode}"
    )

    with pytest.raises(AssertionError, match="tool error"):
        _assert_legacy_credential_grep_clean(result, _PLACEHOLDER_NAME)


def test_real_hit_is_reported_with_paths():
    """rc=0（真有命中）必须失败，且失败文案要列出真实命中的文件路径。"""
    result = LegacyCredentialGrepResult(
        returncode=0,
        hits=["scripts/foo.sh", "src/akb2/bar.py"],
        stderr="",
    )

    with pytest.raises(AssertionError) as exc_info:
        _assert_legacy_credential_grep_clean(result, _PLACEHOLDER_NAME)

    message = str(exc_info.value)
    assert "scripts/foo.sh" in message
    assert "src/akb2/bar.py" in message
