# 检索 HTTP Runbook（同事 Cindy 插件直连 Mini）

给 Cindy 插件用的只读 JSON API。不是网页，没有 HTML UI。

## 启动

凭证只从环境变量读，不接受 CLI 参数（避免出现在 `ps` 里）。变量名是
`AKB2_RETRIEVAL_TOKEN`，最短 32 字符。绑定地址变量是 `AKB2_RETRIEVAL_BIND`，
缺省 `127.0.0.1`；同事要从内网连 Mini 时改成 Mini 的内网 IP。

```text
PYTHONPATH=src python3 -m akb2.retrieval.cli serve --vault-root "$AKB2_ROOT"
```

生产走 LaunchAgent：`launchd/com.akb2.retrieval-http.plist`，包装脚本
`scripts/akb2-retrieval-http.sh` 读 `~/.config/akb2/retrieval.env`。

缺凭证、凭证过短、或绑定全接口地址时进程拒绝监听（退出码为 2）。

## 接口

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/v1/health` | 无 | 探活 |
| GET/POST | `/v1/search` | Bearer | 检索 promoted 笔记 |
| GET | `/v1/note?path=` | Bearer | 读一篇 promoted 笔记 |

检索 JSON 与 CLI 契约相同（`query` / `limit` / `count` / `results`），另加 `source=live`。
`results` 单条字段是 `path` / `title` / `how_density` / `how_executability` / `score` / `related_concepts` / `graph_links`。
缺省不传 `limit`（或 `limit<=0`）返回全部命中；只有调用方显式给正整数才截断。

无凭证或凭证不对 → HTTP 401，body `{"error":"unauthorized"}`，不降级成空结果。

## 绑定纪律

只绑 `127.0.0.1` 或明确的内网单播地址。全接口绑定被启动闸拒绝。
