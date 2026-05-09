# Path 约定

> 占位 — 迁移完成后填实质内容。

## 核心原则

工程脚本 **不得硬编码** vault 路径。一律通过 `config/vault.toml` 解析。

## vault.toml 用法

```python
import tomllib
from pathlib import Path

with open("config/vault.toml", "rb") as f:
    cfg = tomllib.load(f)

vault_root = Path(cfg["vault"]["akb2_root"])
wiki_dir = Path(cfg["vault"]["layers"]["wiki"])
```

## 环境变量

| 变量 | 用途 |
|------|------|
| `${HOME}` | 跨设备路径解析 |
| `${vault.akb2_root}` | vault.toml 内置变量 |

## TODO

- [ ] 写一个 `src/akb2/_paths.py` 工具模块封装 vault 路径解析
- [ ] 单元测试覆盖 vault 路径解析
