"""f2-source-realign：main_sources.yaml 换血闸门。

game-developer / css-tricks / css-irl → down（notes 带日期理由）；
lost-garden 保留观察 lane=design；
ux-collective / vasilis / kilian-valkhof 维持 down；
figma / linear / raycast / emil-kowalski / maggie-appleton 仍 trial。
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SOURCES_PATH = REPO / "config" / "main_sources.yaml"


def _sources() -> dict[str, dict]:
    data = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "sources" in data
    by_slug = {s["slug"]: s for s in data["sources"]}
    assert len(by_slug) == len(data["sources"]), "slug 必须唯一"
    return by_slug


def _assert_down_with_dated_reason(src: dict, slug: str) -> None:
    assert src["status"] == "down", f"{slug} 应为 down, 实际 {src.get('status')}"
    notes = str(src.get("notes") or "")
    assert "2026-08-20" in notes, f"{slug} notes 缺日期理由: {notes}"
    assert notes.strip(), f"{slug} notes 为空"


def test_source_realign_down_css_and_game_developer():
    by_slug = _sources()
    for slug in ("game-developer", "css-tricks", "css-irl"):
        assert slug in by_slug, f"缺源 {slug}"
        _assert_down_with_dated_reason(by_slug[slug], slug)


def test_source_realign_lost_garden_kept_as_design_observe():
    src = _sources()["lost-garden"]
    assert src["status"] == "trial"
    assert src.get("lane") == "design"


def test_source_realign_existing_down_unchanged():
    by_slug = _sources()
    for slug in ("ux-collective", "vasilis", "kilian-valkhof"):
        assert by_slug[slug]["status"] == "down", slug


def test_source_realign_design_engineering_still_trial():
    by_slug = _sources()
    for slug in (
        "figma-blog",
        "linear-blog",
        "raycast-blog",
        "emil-kowalski",
        "maggie-appleton",
    ):
        assert by_slug[slug]["status"] == "trial", slug
