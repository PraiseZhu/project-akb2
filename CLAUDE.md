# AKB2

> 创建日期：2026-05-08
> 技术栈：python

## 项目目标

AKB2.0 工程根：lint/gate/intake CLI + MCP wrapper + tests + eval harness + engineering docs（不含知识库本体）

## 技术栈

- **主语言**：python

## 目录约定

```
Project AKB2/
├── CLAUDE.md           # 本文件 — 项目指引
├── VERSIONING.md       # 版本管理规范
├── README.md           # 用户向项目说明
├── .gitignore          # Git 忽略规则
├── src/                # 源代码
├── docs/               # 文档
├── tests/              # 测试
├── data/               # 数据文件（大数据加 .nosync 后缀）
├── config/             # 配置（不含 .env）
└── history/            # ECC 会话数据（不入 git）
    ├── checkpoints/
    └── daily/
```

## 协作约定

- **AI 助手**：Claude Code（主），其他 provider 通过 `/ask` 调用
- **代码评审**：通过 `/review` 触发
- **测试覆盖**：参见 VERSIONING.md

## 代码规范

- 遵循 `~/.claude/rules/python/` 下的语言规范
- 全局规范：`~/.claude/rules/common/`
- 项目特定 invariants：本节后续追加

## 敏感数据保护

**绝不入 git 的内容**：
- `.env`、`*.key`、`*.pem`、`credentials/`、`*.token.json`、`.auth.json`
- 任何含 API key / OAuth token / 密码的文件
- 用户个人信息（PII）

`.gitignore` 已配置基础排除。新增敏感文件类型时同步更新。

## 版本管理

详见 `VERSIONING.md`。

---

## 项目特定记录

（在此追加项目的具体决策、踩坑、TODO）
