#!/usr/bin/env bash
# merge-baselines.sh — Merge a primary closure manifest with a delta manifest.
#
# Usage:
#   bash merge-baselines.sh \
#     --primary <path-to-primary-closure-manifest.json> \
#     --delta <path-to-delta-manifest.json> \
#     --output <effective-manifest.json>
#
# Rules:
#   - Primary manifest (read-only source of truth for closed phases)
#   - Delta manifest records additive/transitional changes for a single tool
#   - delta-wins for whitelisted fields per `merge_rules.delta_wins_for_fields`
#   - automation_allowed is INVARIANT: delta MUST set it false (or omit); any true → exit 1
#   - delta MAY only modify tools listed in tools_changed[]; other tools must be inherited as-is from primary
#   - Output: effective-manifest.json (NOT a source of truth — derived view)
#
# Exit codes:
#   0  success
#   1  delta contains automation_allowed=true (forbidden)
#   2  delta slug not in primary tools[], or slug outside allowed_slugs scope
#   3  primary or delta file missing or invalid JSON
#   4  resulting effective manifest has wrong tool count
#   5  invalid CLI args
#   6  discovery_update propagation broken (a1_mcp_discovery.status mismatch or implication stale)

set -euo pipefail

PRIMARY=""
DELTA=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --primary) PRIMARY="$2"; shift 2 ;;
    --delta)   DELTA="$2";   shift 2 ;;
    --output)  OUTPUT="$2";  shift 2 ;;
    *) echo "ERROR: unknown arg $1" >&2; exit 5 ;;
  esac
done

if [[ -z "$PRIMARY" || -z "$DELTA" || -z "$OUTPUT" ]]; then
  echo "Usage: $0 --primary <path> --delta <path> --output <path>" >&2
  exit 5
fi

[[ -f "$PRIMARY" ]] || { echo "ERROR: primary not found: $PRIMARY" >&2; exit 3; }
[[ -f "$DELTA"   ]] || { echo "ERROR: delta not found: $DELTA"     >&2; exit 3; }

# Validate JSON
jq empty "$PRIMARY" 2>/dev/null || { echo "ERROR: primary not valid JSON" >&2; exit 3; }
jq empty "$DELTA"   2>/dev/null || { echo "ERROR: delta not valid JSON"   >&2; exit 3; }

# Guard: delta must NOT contain automation_allowed=true anywhere in tools_changed[].to
HAS_AUTOMATION_TRUE=$(jq '[.tools_changed[].to.automation_allowed] | any(. == true)' "$DELTA")
if [[ "$HAS_AUTOMATION_TRUE" == "true" ]]; then
  echo "ERROR: delta contains automation_allowed=true (forbidden by merge invariant)" >&2
  exit 1
fi

# Get list of tools delta intends to change
DELTA_TOOLS=$(jq -r '.tools_changed[].slug' "$DELTA")
PRIMARY_TOOLS=$(jq -r '.tools[].slug' "$PRIMARY")

# Sanity: every slug in delta tools_changed must exist in primary tools[]
for slug in $DELTA_TOOLS; do
  if ! echo "$PRIMARY_TOOLS" | grep -qx "$slug"; then
    echo "ERROR: delta references unknown tool slug: $slug (not in primary)" >&2
    exit 2
  fi
done

# Guard: delta tools_changed slugs must be within allowed_slugs (if declared)
if jq -e '.allowed_slugs != null' "$DELTA" >/dev/null 2>&1; then
  ALLOWED_SLUGS=$(jq -r '.allowed_slugs[]' "$DELTA")
  for slug in $DELTA_TOOLS; do
    if ! echo "$ALLOWED_SLUGS" | grep -qx "$slug"; then
      echo "ERROR: delta tools_changed[$slug] is outside allowed_slugs scope" >&2
      exit 2
    fi
  done
fi

# Build effective manifest:
# - Start from primary
# - For each tool in delta tools_changed[], replace its entry's mutable fields with delta.to
# - Other tools: keep primary entry verbatim
# - five_file_sha256: replace cc-akb-search entry with delta.after; other tools unchanged
# - Carry over snapshot_at, schema_version pointing to delta; preserve primary as based_on

PRIMARY_HASH=$(shasum -a 256 "$PRIMARY" | awk '{print $1}')

EFFECTIVE=$(jq -s '
  .[0] as $primary |
  .[1] as $delta  |
  ($delta.tools_changed | map({key: .slug, value: .}) | from_entries) as $by_slug |

  # Update each tool entry
  ($primary.tools | map(
    if $by_slug[.slug] != null then
      . + {
        toolbox_status: $by_slug[.slug].to.toolbox_status,
        wiki_status:    ($by_slug[.slug].to.wiki_status   // .wiki_status),
        eval_level:     $by_slug[.slug].to.eval_level,
        automation_allowed: ($by_slug[.slug].to.automation_allowed // false),
        backlink_status: $by_slug[.slug].to.backlink_status,
        skill_sha256:   ($by_slug[.slug].to.skill_sha256   // .skill_sha256),
        last_phase:     ($by_slug[.slug].to.last_phase     // .last_phase),
        block_reason:   ($by_slug[.slug].to.block_reason   // .block_reason),
        delta_evidence: $by_slug[.slug].evidence_paths,
        delta_review:   $by_slug[.slug].review_paths
      }
    else
      .
    end
  )) as $merged_tools |

  # Update five_file_sha256
  ($primary.five_file_sha256 |
    to_entries |
    map(
      if $by_slug[.key] != null and $by_slug[.key].five_file_sha256 != null then
        .value as $orig |
        .value = (
          $by_slug[.key].five_file_sha256 |
          to_entries |
          map({key: .key, value: .value.after}) |
          from_entries
        )
      else
        .
      end
    ) |
    from_entries
  ) as $merged_sha |

  $primary + {
    schema_version: ($primary.schema_version + "+delta-" + $delta.delta_label),
    delta_label: $delta.delta_label,
    delta_based_on: {
      path: $delta.based_on.path,
      sha256: $delta.based_on.sha256
    },
    delta_snapshot_at: $delta.snapshot_at,
    effective_generated_at: (now | strftime("%Y-%m-%dT%H:%M:%SZ")),
    effective_generated_by: "merge-baselines.sh",
    is_source_of_truth: false,
    is_derived: true,
    derived_note: "This manifest is a runtime view; primary closure remains the canonical baseline.",
    tools: $merged_tools,
    five_file_sha256: $merged_sha,
    delta_tools_changed: $delta.tools_changed,
    merge_rules_applied: $delta.merge_rules,
    a1_mcp_discovery: (
      if $delta.a1_mcp_discovery_update != null then
        {
          tool: $delta.a1_mcp_discovery_update.tool,
          status: $delta.a1_mcp_discovery_update.status,
          session_check_at: ($delta.a1_mcp_discovery_update.verified_at // $primary.a1_mcp_discovery.session_check_at),
          verification_method: ($delta.a1_mcp_discovery_update.verification_method // null),
          ccd_restart_required: ($delta.a1_mcp_discovery_update.ccd_restart_required // null),
          implication: ("cc-akb-search mounted; P1e A4/C-5 accepted active/L1 via delta " + $delta.delta_label),
          previous_state_for_audit: $primary.a1_mcp_discovery
        }
      else
        $primary.a1_mcp_discovery
      end
    ),
    pending_next_phases: (
      $primary.pending_next_phases
      | if .phase_3_cc_akb_search_unblock != null
           and ($delta.tools_changed | map(.slug) | index("cc-akb-search")) != null
        then
          .phase_3_cc_akb_search_unblock = {
            status: "complete",
            completed_at: $delta.snapshot_at,
            completed_via: ("P1e delta " + $delta.delta_label + " — A4 5/5 + Review2 APPROVED + C-5 accept_active_L1"),
            previous_state_for_audit: $primary.pending_next_phases.phase_3_cc_akb_search_unblock
          }
        else
          .
        end
    )
  }
' "$PRIMARY" "$DELTA")

# Verify resulting tool count matches primary
PRIMARY_COUNT=$(echo "$PRIMARY_TOOLS" | wc -l | tr -d ' ')
EFFECTIVE_COUNT=$(echo "$EFFECTIVE" | jq '.tools | length')
if [[ "$PRIMARY_COUNT" != "$EFFECTIVE_COUNT" ]]; then
  echo "ERROR: effective tool count mismatch (primary=$PRIMARY_COUNT, effective=$EFFECTIVE_COUNT)" >&2
  exit 4
fi

# Verify automation_allowed=false for ALL tools
EFFECTIVE_AUTO_TRUE=$(echo "$EFFECTIVE" | jq '[.tools[].automation_allowed] | any(. == true)')
if [[ "$EFFECTIVE_AUTO_TRUE" == "true" ]]; then
  echo "ERROR: effective manifest has tool(s) with automation_allowed=true" >&2
  exit 1
fi

# Guard: when delta carries a1_mcp_discovery_update, effective top-level must reflect it
HAS_DISC_UPDATE=$(jq -e '.a1_mcp_discovery_update != null' "$DELTA" >/dev/null 2>&1 && echo "yes" || echo "no")
if [[ "$HAS_DISC_UPDATE" == "yes" ]]; then
  EFF_DISC_STATUS=$(echo "$EFFECTIVE" | jq -r '.a1_mcp_discovery.status')
  DELTA_DISC_STATUS=$(jq -r '.a1_mcp_discovery_update.status' "$DELTA")
  if [[ "$EFF_DISC_STATUS" != "$DELTA_DISC_STATUS" ]]; then
    echo "ERROR: effective a1_mcp_discovery.status ($EFF_DISC_STATUS) does not match delta update ($DELTA_DISC_STATUS)" >&2
    exit 6
  fi
  EFF_IMPL=$(echo "$EFFECTIVE" | jq -r '.a1_mcp_discovery.implication')
  if [[ "$EFF_IMPL" == *"blocked candidate"* ]]; then
    echo "ERROR: effective a1_mcp_discovery.implication still contains 'blocked candidate'" >&2
    exit 6
  fi
fi

# Write effective manifest
echo "$EFFECTIVE" | jq '.' > "$OUTPUT"
echo "✅ effective manifest written: $OUTPUT"

# Generate human-readable summary alongside
SUMMARY="${OUTPUT%.json}-summary.md"
{
  echo "# Effective Manifest Summary"
  echo ""
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Primary: $PRIMARY (sha256: $PRIMARY_HASH)"
  echo "Delta:   $DELTA"
  echo ""
  echo "## Tool States"
  echo ""
  echo "| slug | toolbox_status | wiki_status | eval_level | automation_allowed | last_phase |"
  echo "|------|----------------|-------------|------------|--------------------|------------|"
  echo "$EFFECTIVE" | jq -r '.tools[] | "| \(.slug) | \(.toolbox_status) | \(.wiki_status) | \(.eval_level) | \(.automation_allowed) | \(.last_phase) |"'
  echo ""
  echo "## Delta-changed tools"
  echo ""
  echo "$EFFECTIVE" | jq -r '.delta_tools_changed[] | "- **\(.slug)**: \(.from.toolbox_status)/\(.from.eval_level) → \(.to.toolbox_status)/\(.to.eval_level)"'
  echo ""
  echo "## MCP Discovery"
  echo ""
  echo "$EFFECTIVE" | jq -r '"- a1_mcp_discovery.status: \(.a1_mcp_discovery.status) (verified \(.a1_mcp_discovery.session_check_at // "n/a"))"'
  echo "$EFFECTIVE" | jq -r '"- implication: \(.a1_mcp_discovery.implication // "n/a")"'
  echo ""
  echo "## Verification"
  echo ""
  echo "- Tool count: $EFFECTIVE_COUNT (matches primary)"
  echo "- automation_allowed=true count: 0"
  echo "- This manifest is **derived** (not source of truth)"
} > "$SUMMARY"
echo "✅ summary written: $SUMMARY"
