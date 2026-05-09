#!/usr/bin/env bash
# evaluator-mock.sh — deterministic mock evaluator for P1d L1 smoke
#
# Reads stdin JSON: {skills: [{name, description, use_7d, use_30d, mtime, ...}, ...]}
# Outputs stdout JSON array of {skill_name, verdict, reason}
#
# verdict ∈ {Keep, Improve, Update, Retire, "Merge into <X>"}
# reason ≥ 30 chars, contains skill_name, no forbidden tokens (unchanged|same|as before|n/a|no change)
#
# 0 LLM call. 0 network. 0 localhost dependency. Pure Bash + jq.
# Hardcoded verdict table for fixture skills:
#   skill-a → Keep
#   skill-b → Improve
#   skill-c → Retire
# unknown skill name → exit 2

set -euo pipefail

# Read all stdin
input=$(cat)

# Validate JSON
if ! echo "$input" | jq empty 2>/dev/null; then
  echo "Error: stdin is not valid JSON" >&2
  exit 1
fi

# Validate schema: must have .skills array
if ! echo "$input" | jq -e '.skills | type == "array"' > /dev/null 2>&1; then
  echo "Error: stdin .skills must be a JSON array" >&2
  exit 1
fi

# Get list of skill names
names=$(echo "$input" | jq -r '.skills[].name')

# Validate every skill name is in hardcoded table
for n in $names; do
  case "$n" in
    skill-a|skill-b|skill-c) ;;
    *)
      echo "Error: unknown skill name '$n' (only fixture skill-a/skill-b/skill-c supported)" >&2
      exit 2
      ;;
  esac
done

# Build hardcoded verdict + reason table
result='[]'
for n in $names; do
  case "$n" in
    skill-a)
      verdict="Keep"
      reason="fixture skill-a 描述完整、近 7 天 12 次调用、frontmatter aliases 齐全 — 当前指标支持保持现状,不需要重构或合并。"
      ;;
    skill-b)
      verdict="Improve"
      reason="fixture skill-b 30 天 0 次调用且 description 仅 3 字符,建议补 description + 加 alias 提升 discoverability。"
      ;;
    skill-c)
      verdict="Retire"
      reason="fixture skill-c 90 天无调用、frontmatter 已标 deprecated、与 skill-a 功能高度重叠 — 建议归档释放 trigger 表。"
      ;;
  esac

  result=$(echo "$result" | jq \
    --arg n "$n" \
    --arg v "$verdict" \
    --arg r "$reason" \
    '. + [{skill_name: $n, verdict: $v, reason: $r}]')
done

echo "$result"
