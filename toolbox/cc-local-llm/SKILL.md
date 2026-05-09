---
name: "cc-local-llm"
description: "llama-swap 本地 LLM 集成。通过 127.0.0.1:8080 的 OpenAI-compatible /v1/* 端点进行只读本地推理,包含健康检查、chat 调用模板、参数经验值、长文本分批策略、结构化 JSON 抽取、system prompt 注入上下文。"
---

<!-- akb-backlink: /Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/wiki/tools/cc-local-llm.md -->
<!-- akb-interface: /Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/toolbox/interfaces/cc-local-llm.md -->

# 本地LLM

## Trigger

This skill may be used when the user request matches the description or aliases.

Aliases: `本地LLM`, `cc-local-llm`, `local-llm`

## Purpose

llama-swap 本地 LLM 集成。通过 `http://127.0.0.1:8080` 的 OpenAI-compatible `/v1/*` 端点进行只读本地推理,包含健康检查、chat 调用模板、参数经验值、长文本分批策略、从自由文本提取结构化 JSON、system prompt 注入上下文。

当前后端契约:

- backend: `llama-swap` 代理 `llama.cpp / llama-server`
- base_url: `http://127.0.0.1:8080`
- health: `GET /health` 期望返回 body `OK`
- models: `GET /v1/models` 期望返回 OpenAI-compatible JSON
- chat: `POST /v1/chat/completions`(OpenAI-compatible messages schema)
- preferred chat model: `qwen3-8b`
- fallback chat models: `qwen2.5-14b`, `qwen3-32b`
- embedding models: `bge-m3`, `nomic-embed`;不得用于 chat smoke
- network_required: `false`;只访问 localhost
- automation_allowed: `false`;不得作为自动化任务入口
- runtime_mutation_allowed: `false`;不得启动、停止、重启后端,不得修改模型配置,不得拉取或安装模型

## Workflow

1. Confirm the user intent matches this skill and check the dependency notes below.
2. Confirm local readiness with read-only requests when runtime access is required: `GET /health` and `GET /v1/models`.
3. Use `POST /v1/chat/completions` with the OpenAI-compatible messages schema for chat tasks.
4. Prefer `qwen3-8b` for lightweight summary, classification, extraction, and short QA; fall back to `qwen2.5-14b` or `qwen3-32b` only when the preferred model is unavailable or task size requires it.
5. For long text, split input into chunks, summarize each chunk, then synthesize the final answer; keep each model response under an explicit length or schema assertion when testing.
6. Prefer Codex-native tools and current workspace instructions over Claude-only slash commands or tool names from the source skill.
7. Read `references/original-skill.md` only when the lean instructions here are not enough, and do not inherit stale backend details from it.
8. Keep generated work inside the current user-approved workspace unless the user explicitly directs otherwise.

## Dependencies

- Local process expected to be reachable at `http://127.0.0.1:8080`.
- No external network, login, secret, model install, model configuration edit, or runtime lifecycle action is required.

## Safety

- Install status: `convert_then_install`; risk: `low`.
- Source flags: non_ascii_or_non_slug_name, claude_runtime_terms.
- Do not assume Claude Code slash commands, hooks, MCP servers, or `~/.claude` runtime behavior exists in Codex.
- Treat localhost model calls as read-only inference. `runtime_mutation_allowed` is false.
- Do not run this skill as an automation. `automation_allowed` is false until a separate gate explicitly changes it.
- Do not start, stop, restart, reconfigure, or otherwise mutate the local model backend.
- Do not change model routing, install models, edit backend config, or bypass existing runtime locks.
- Do not use or create secrets. Ask for explicit user direction before any external login, deployment, destructive command, or persistent configuration change.

## Source Notes

- Original Claude skill: `本地LLM`.
- Source metadata name: `本地LLM`.
- Source hash: `bb91bb76ef2c008c9667b58bab87acaf6864f259c18fc46afba2fb93f3be58af`.
- The original skill description referenced a legacy backend口径 that has been superseded by the current llama-swap / 8080 contract above. See AKB2.0 correction report `docs/phase-c-reports/p1c-local-llm-backend-contract-refresh-2026-05-07.md` for the specific superseded anchors.
- The original skill is available for reference only; do not inherit its backend description.
- No bundled source resources were copied for active use.
