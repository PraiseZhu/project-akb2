"""统一 tweet schema + YAML 配置加载 + collector 基类。"""
import json
import os
import yaml
from abc import ABC, abstractmethod
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent  # worktree root


def load_config(path: str = "config/intake_sources.yaml") -> dict:
    """加载 intake 配置，返回 dict。"""
    with open(Path(REPO_ROOT) / path) as f:
        return yaml.safe_load(f)


def make_tweet(id: str, text: str, author: str, created_at: str, **kwargs) -> dict:
    """生成符合 social_trends_v1_1 tweets.items schema 的 tweet dict。"""
    t = {"id": id, "text": text, "author": author, "created_at": created_at}
    t.update(kwargs)
    return t


class BaseCollector(ABC):
    """所有 collector 的基类。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()

    @abstractmethod
    def collect(self, dry_run: bool = False) -> list[dict]:
        """返回 tweet list，每条符合 social_trends_v1_1 tweets.items schema。"""
        ...

    def run_cli(self, output: str, dry_run: bool = False) -> None:
        """CLI 入口：collect 并写 JSON 到 output 路径。"""
        tweets = self.collect(dry_run=dry_run)
        result = {
            "date": tweets[0]["created_at"][:10] if tweets else "2026-05-27",
            "source": self.source_name,
            "tweets": tweets,
        }
        with open(output, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Written {len(tweets)} tweets to {output}")

    @property
    def source_name(self) -> str:
        return self.__class__.__name__
