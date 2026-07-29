#!/usr/bin/env bash
# 机器本地状态根解析 + 同步范围守卫 —— shell 侧唯一实现。
#
# 为什么要有这个文件（2026-07-28 /simplify 复核）：
#   akb2-main-pipeline.sh / bootstrap_launchd.sh / deploy-mac-mini.sh 各自复制了
#   一份逐字相同的 STATE_ROOT 解析 + 同步范围 case，而三份都**漏了** python 侧
#   `state_paths._VAULT_ENV_NAMES` 会检查的 AKB2_ROOT / AKB2_VAULT_ROOT / VAULT_ROOT
#   —— 三处注释里写的「与 python 侧 get_state_root() 一致」在写下的当刻就不成立。
#   这是安全判定：漏判一次 = 状态又落回 Syncthing（`data/star_history.sqlite` 已
#   实证被同步覆盖丢过 690 行写入）。
#
# 用法：
#   source "$(dirname "$0")/lib/state-root.sh"
#   akb2_resolve_state_root                  # 设 $STATE_ROOT
#   akb2_state_root_is_synced || ...         # 返回 0 = 在同步范围内（危险）
# 报错文案与退出码留给各调用方（三处的 FATAL/exit 1、ERROR/exit 2、fail() 各不同）。
#
# 与 python 侧的对应关系：
#   akb2_resolve_state_root  ↔ state_paths.get_state_root()
#   akb2_state_root_is_synced ↔ state_paths.is_synced_path()
# 判据清单改动时**两侧必须同改**；`tests/test_shell_state_paths.py` 有 parity 断言。

# 解析机器本地状态根到 $STATE_ROOT（AKB2_STATE_ROOT 优先，否则 macOS 标准位置）。
akb2_resolve_state_root() {
    STATE_ROOT="${AKB2_STATE_ROOT:-$HOME/Library/Application Support/akb2}"
}

# 判断 $1（缺省 $STATE_ROOT）是否落在已知跨机同步范围内。
# 返回 0 = 在同步范围内（危险，调用方应 fail-closed）；返回 1 = 安全。
akb2_state_root_is_synced() {
    local target="${1:-$STATE_ROOT}"
    case "$target" in
        # 与 _SYNCED_HOME_RELATIVE / _SYNCED_ABSOLUTE 对齐
        "$HOME/AI-Agent"|"$HOME/AI-Agent"/*) return 0 ;;
        "$HOME/Library/Mobile Documents"|"$HOME/Library/Mobile Documents"/*) return 0 ;;
        /Volumes/AKB2|/Volumes/AKB2/*) return 0 ;;
    esac
    # 与 _VAULT_ENV_NAMES 对齐：vault 也可能由这三个环境变量指定（三份旧拷贝都漏了）
    local name value
    for name in AKB2_ROOT AKB2_VAULT_ROOT VAULT_ROOT; do
        eval "value=\${$name:-}"
        [ -n "$value" ] || continue
        case "$target" in
            "$value"|"$value"/*) return 0 ;;
        esac
    done
    return 1
}
