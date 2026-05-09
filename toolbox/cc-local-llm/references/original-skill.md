---
name: 本地LLM
description: Ollama 本地 LLM 集成。包含可用性检测与自动启动、/api/chat 调用模板、参数经验值、长文本分批策略、从自由文本提取结构化 JSON、system prompt 注入上下文。当用户要求调用本地 LLM、使用 Ollama 生成内容、解析 LLM 输出为结构化数据时使用。
---

# Ollama 本地 LLM 集成

## 可用性检测 + 自动启动

```javascript
async function ensureOllamaRunning(baseUrl = "http://localhost:11434") {
  try {
    const resp = await fetch(`${baseUrl}/`, { signal: AbortSignal.timeout(3000) });
    if (resp.ok) return;
  } catch {}

  // macOS 自动启动
  try {
    const { execSync } = await import("node:child_process");
    execSync("open /Applications/Ollama.app", { stdio: "ignore" });
  } catch {}

  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const resp = await fetch(`${baseUrl}/`, { signal: AbortSignal.timeout(3000) });
      if (resp.ok) return;
    } catch {}
  }
  throw new Error("Ollama 启动超时（60秒）");
}
```

## /api/chat 调用模板

```javascript
async function callOllama(baseUrl, model, messages, options = {}) {
  const resp = await fetch(`${baseUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(300_000),
    body: JSON.stringify({
      model,
      messages,
      stream: false,
      options: { temperature: 0.3, num_ctx: 16384, ...options },
    }),
  });
  if (!resp.ok) throw new Error(`Ollama error: ${resp.status}`);
  const data = await resp.json();
  return data.message?.content || "";
}
```

## 参数经验值

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `num_ctx` | 16384 | 上下文窗口，低于 8192 容易截断 |
| `temperature` | 0.1-0.3 | 摘要/分析用低值；创意内容可提高 |
| `stream` | false | 脚本场景始终关闭 |
| 超时 | 300s | 大文本分析需要较长时间 |
| 危机评估超时 | 60s | 轻量 prompt 可缩短 |

## 长文本分批处理

当帖子/文档数量超过 30 条时：

```javascript
const BATCH_SIZE = 30;

async function processLargeInput(items, processFunc) {
  if (items.length <= BATCH_SIZE) return processFunc(items);

  const batchResults = [];
  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    batchResults.push(await processFunc(batch));
  }
  return await mergeResults(batchResults);
}
```

合并时 system prompt 要求"去重、合并同类项、保留最重要的内容"。

## 从 LLM 输出提取结构化 JSON

LLM 输出通常包含解释文字 + JSON，需要正则提取。

### 提取对象

```javascript
const raw = llmOutput;
const jsonMatch = raw.match(/\{[\s\S]*\}/);
if (jsonMatch) {
  const result = JSON.parse(jsonMatch[0]);
}
```

### 提取数组

```javascript
const jsonMatch = raw.match(/\[[\s\S]*\]/);
if (jsonMatch) {
  const entries = JSON.parse(jsonMatch[0]);
}
```

始终用 `try/catch` 包裹，LLM 可能输出无效 JSON。

## System Prompt 注入上下文

向 system prompt 动态注入领域知识（如社区用语词典）：

```javascript
function buildSystemPrompt(context, dictionary = "") {
  let prompt = `你是${context}分析师。`;
  if (dictionary) {
    prompt += `\n\n以下是领域术语词典：\n${dictionary}`;
  }
  prompt += `\n\n输出要求：使用中文，格式为 Markdown。`;
  return prompt;
}
```

## Prompt 模板示例

### 摘要生成

system: 分析师角色 + 输出格式要求 + 词典注入
user: 数据列表 + "请分类汇总 + 氛围评估 + 核心洞察"

### 危机评估

system: "只输出 JSON：`{is_crisis, severity, summary, details}`"
user: 数据列表
options: `temperature: 0.1, num_ctx: 8192`

### 实体提取

system: "只输出 JSON 数组：`[{term, meaning}]`"
user: 原始文本
