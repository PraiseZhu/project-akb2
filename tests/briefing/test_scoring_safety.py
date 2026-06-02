"""P1-6 BANNED 精确化：不误杀含 exploit/hack 的 AI 安全内容，真违禁仍 safety=0。"""
from __future__ import annotations

from akb2.briefing import scoring


def _t(text):
    return {"text": text}


# ---------------------------------------------------------------------------
# SC-1：含 exploit / hack 的 AI 安全内容不被误杀（safety=1.0）
# ---------------------------------------------------------------------------

def test_ai_security_exploit_not_banned():
    t = _t("New paper on prompt-injection exploits in LLM agents and how to defend against them.")
    assert scoring.safety(t) == 1.0


def test_ai_security_hack_not_banned():
    t = _t("Red-teaming our model: we hacked it to find jailbreaks before release.")
    assert scoring.safety(t) == 1.0


def test_word_boundary_no_substring_false_positive():
    # "scampi" 含 "scam" 子串，但词边界不应误判
    assert scoring.safety(_t("Best garlic scampi recipe for the weekend.")) == 1.0


# ---------------------------------------------------------------------------
# SC-2：真违禁内容仍 safety=0
# ---------------------------------------------------------------------------

def test_true_malware_banned():
    assert scoring.safety(_t("Selling ransomware kits, DM for the malware builder.")) == 0.0


def test_true_phishing_banned():
    assert scoring.safety(_t("Click this phishing link to claim your prize.")) == 0.0


def test_true_nsfw_banned():
    assert scoring.safety(_t("Free nsfw content here.")) == 0.0


def test_illegal_banned():
    assert scoring.safety(_t("How to do illegal things, totally not safe.")) == 0.0
