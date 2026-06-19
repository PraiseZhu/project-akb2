"""AKB2 Quality Intake — 抓取与 raw 锚点化（循环 1 段 1-2）。

模块:
    discovery:    RSS/Atom 发现 + 指纹去重 + 产量隔离
    extract:      defuddle → Scrapling → Docling 决策树
    raw_archiver: raw 文件锚点化归档（fail-closed）
    source_validator: 逐源验证 CLI
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


def _check_tool_available(
    name: str,
    probe: Callable[[], None] | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    """探测 Python 包是否真实可用（不仅 importable，还可通过 probe 验证运行时）。

    比 find_spec 更严格：部分包（如 docling）因缺少运行时依赖可以 import 但无法
    实例化核心类。调用方可传入 probe 回调做一次真实实例化，失败则返回 False。

    不抛异常；不可用时记录一条 warning 并返回 False——warning 经 `logger` 参数发出
    （调用方传入自身 logger，使日志归属于调用模块，避免上下游各发一条重复警告）。
    未传 logger 时回退到本模块 logger。不缓存：probe 涉及 sys.modules 注入（测试环境），
    缓存会破坏隔离性。

    Args:
        name:   Python 包名（用于 find_spec 检测）
        probe:  可选的运行时探针，调用时应实例化一次核心对象。
                失败即视为不可用（RUNTIME_BLOCKED）。
        logger: warning 输出所用 logger；None 时用本模块 logger。
    """
    log = logger if logger is not None else _LOGGER

    if importlib.util.find_spec(name) is None:
        log.warning(
            "%s not available in current Python env; related extraction will be skipped",
            name,
        )
        return False

    if probe is not None:
        try:
            probe()
        except Exception as exc:
            log.warning(
                "%s importable but probe() failed (%s); "
                "related extraction will be skipped (%s_RUNTIME_BLOCKED)",
                name, exc, name.upper(),
            )
            return False

    return True
