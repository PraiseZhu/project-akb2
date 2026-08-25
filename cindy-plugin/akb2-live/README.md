# AKB2 Live（Cindy 插件）

同事装这个插件后，在 Cindy 里搜 Mini 上实时的 promoted 笔记。

不是网页。插件的 Node worker 直连 Mini 的只读 HTTP：

`http://<Mini 内网 IP>:8787/v1/search`

## 同事怎么用

1. 确认能 `ping 172.26.163.197`（或当前 Mini 内网 IP）。
2. 装 `.cindy` 包，打开插件设置：
   - Mini 地址 / 端口
   - 检索 token（与 Mini `~/.config/akb2/retrieval.env` 同一份）
3. 聊天里 `$akb2` 或让 Agent 调 `search` / `get`。

Mini 连不上时，worker 会改搜包内 `snapshot.json`（`source=snapshot`，带 `degraded=true`）。快照要重新打包才更新。

## Mini 侧

```bash
cp config/retrieval.env.example ~/.config/akb2/retrieval.env
# 填 AKB2_RETRIEVAL_TOKEN（≥32 字符）和 AKB2_RETRIEVAL_BIND=<内网 IP>
chmod 600 ~/.config/akb2/retrieval.env
bash scripts/bootstrap_launchd.sh --plist-src launchd/com.akb2.retrieval-http.plist
```

刷新快照（可选）：

```bash
PYTHONPATH=src python3 scripts/pack-akb2-live-snapshot.py --vault-root "$AKB2_ROOT"
```
