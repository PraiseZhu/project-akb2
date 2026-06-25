---
artifact_type: raw
source_slug: trap-mcp-deploy
source_url_primary: https://example.com/trap-mcp-deploy-runbook
fetched_at: 2026-06-23
fetched_via: claude_code_webfetch
---

# MCP server 部署踩坑记录

部署 MCP server 时有一堆杂事要做。这篇记录前面铺了大量次要操作（环境准备 + 边角检查），核心部署命令藏在最后一个代码块。用来验证 scan_all 能抽核心不被次要带偏。

## 环境准备（次要操作）

先把脚手架搭好，跑下面这些：

```bash
git clone https://github.com/example/repo.git
cd repo
npm install
npm run build
ls -la
cat package.json
echo $PATH
mkdir -p ~/.config/mcp
cp config.json ~/.config/mcp/
chmod 600 ~/.config/mcp/config.json
```

## 一些边角检查（次要操作）

随手跑几条看看状态：

```bash
git status
git log --oneline -5
curl -s localhost:8080/health
docker ps
docker images
```

## 核心部署步骤

真正关键的一步在最后。所有前面的命令都是铺垫，核心是下面这个代码块里的 `mcp-server init`：

```bash
mcp-server init --config ~/.config/mcp/config.json
mcp-server tools list
mcp-server tools call search
```

只有 `mcp-server init` 跑通了，server 才算活过来，然后才能 list / call tools。前面 15 条次要命令都是辅助。

## 边界

仅限生产环境使用。不支持开发环境直接跑。

## 结论

因此，部署 MCP server 的核心是 `mcp-server init` 这一步，其余都是辅助。关键是 init 必须在 tools list 之前完成。

---

## 抓取元信息

| 字段 | 值 |
|---|---|
| fetched_at | 2026-06-23 |
| fetched_via | Claude Code WebFetch（人工构造的陷阱样本语料，用于 step-8 回归测试） |
| 语料用途 | 两个 bash 代码块装 15 条次要操作（git/npm/ls/cat/curl/docker），核心 `mcp-server init` 藏第三个代码块末尾；验证 scan_all 抽核心不被次要带偏 + must_cover 三类全命中 |
