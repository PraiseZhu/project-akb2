"""loop2 Maker 组装层：行预算 / 抽取合同 / 定点修 / compose2。"""

from .compose2 import Compose2Result, compose_candidate
from .extract_contract import ExtractContractResult, fulfill_extract_contract
from .fixpoint import FixpointResult, run_fixpoint
from .frontmatter_budget import (
    FrontmatterBudgetError,
    apply_frontmatter_budget,
    count_frontmatter_lines,
    dump_frontmatter,
)

__all__ = [
    "Compose2Result",
    "ExtractContractResult",
    "FixpointResult",
    "FrontmatterBudgetError",
    "apply_frontmatter_budget",
    "compose_candidate",
    "count_frontmatter_lines",
    "dump_frontmatter",
    "fulfill_extract_contract",
    "run_fixpoint",
]
