#!/usr/bin/env bash
set -euo pipefail

FIXTURE_DIR="tests/fixtures/intake/dry-run-2026-05-27"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Credential resolution helpers (no literal secrets here — all from env)
_x_bearer() {
  echo "${X_BEARER_TOKEN:-${TWITTER_BEARER_TOKEN:-}}"
}

_llm_key() {
  echo "${AI_GATEWAY_API_KEY:-}"
}

case "${1:-}" in
  --collect)
    echo "Collecting from X/Twitter..."
    BEARER="$(_x_bearer)"
    if [[ -z "$BEARER" ]]; then
      echo "WARNING: No X API credentials found (X_BEARER_TOKEN / TWITTER_BEARER_TOKEN)."
      echo "         Existing synthetic fixture at: $FIXTURE_DIR/sample.json"
      echo "         Set X_BEARER_TOKEN or TWITTER_BEARER_TOKEN and re-run to collect real data."
      exit 0
    fi
    echo "Querying Twitter/X API v2 recent search..."
    RESPONSE=$(curl -s -G \
      "https://api.twitter.com/2/tweets/search/recent" \
      --data-urlencode "query=AI OR LLM OR AGI lang:en -is:retweet" \
      --data-urlencode "max_results=20" \
      --data-urlencode "tweet.fields=created_at,public_metrics,author_id" \
      --data-urlencode "expansions=author_id" \
      --data-urlencode "user.fields=username" \
      -H "Authorization: Bearer $BEARER")
    mkdir -p "$REPO_ROOT/$FIXTURE_DIR"
    echo "$RESPONSE" > "$REPO_ROOT/$FIXTURE_DIR/sample_raw.json"
    echo "Raw API response saved to: $FIXTURE_DIR/sample_raw.json"
    echo "NOTE: Transform sample_raw.json to sample.json format using the intake collector (step-2)."
    ;;

  --synthesize)
    echo "Running DeepSeek synthesis..."
    LLM_KEY="$(_llm_key)"
    if [[ -z "$LLM_KEY" ]]; then
      echo "WARNING: No LLM API credentials found (AI_GATEWAY_API_KEY)."
      echo "         Existing synthetic fixture at: $FIXTURE_DIR/deepseek-output.json"
      echo "         Set AI_GATEWAY_API_KEY and re-run for real model output."
      exit 0
    fi
    SAMPLE_PATH="$REPO_ROOT/$FIXTURE_DIR/sample.json"
    if [[ ! -f "$SAMPLE_PATH" ]]; then
      echo "ERROR: $SAMPLE_PATH not found. Run --collect first." >&2
      exit 1
    fi
    echo "Calling DeepSeek via AI Gateway..."
    python3 - "$SAMPLE_PATH" "$REPO_ROOT/$FIXTURE_DIR/deepseek-output.json" \
              "$LLM_KEY" <<'PYEOF'
import json, sys, urllib.request, urllib.error

sample_path, out_path, api_key = sys.argv[1], sys.argv[2], sys.argv[3]

sample = json.load(open(sample_path))
tweets = sample.get("tweets", [])[:5]
tweets_text = "\n".join(
    f"[{t['id']}] @{t['author']}: {t['text']}" for t in tweets
)
total = len(sample.get("tweets", []))

import os
base = os.environ.get("AI_GATEWAY_BASE_URL", "https://llm-proxy.tapsvc.com").rstrip("/")
url = f"{base}/v1/chat/completions"
model = "deepseek/deepseek-v4-pro"

payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "You are an AI trend analyst. Output ONLY valid JSON matching the social_trends_v1_1 schema. No markdown, no explanation."},
        {"role": "user", "content": (
            f"Analyze these tweets and output a JSON object with ALL required fields:\n"
            f"- date: \"2026-05-27\"\n"
            f"- generated_at: current ISO timestamp\n"
            f"- source: {{\"name\": \"deepseek_synthesis\", \"collector_version\": \"1.0.0\"}}\n"
            f"- scoring_summary: {{\"total_tweets\": {total}, \"scored_tweets\": 5}}\n"
            f"- tweets: array of tweet objects (each with id, author, text, created_at)\n"
            f"- focus_discussions: array of 2-3 discussion objects\n\n"
            f"Tweets:\n{tweets_text}\n\nOutput only the JSON object."
        )}
    ],
    "temperature": 0.2,
    "response_format": {"type": "json_object"}
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
})
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]
        output = json.loads(content)
except urllib.error.HTTPError as e:
    print(f"HTTP error {e.code}: {e.read().decode()}", file=sys.stderr)
    sys.exit(1)

with open(out_path, "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"Output saved to: {out_path}")
PYEOF
    ;;

  --check)
    MISSING=0
    for f in sample.json deepseek-output.json README.md; do
      if [[ ! -f "$REPO_ROOT/$FIXTURE_DIR/$f" ]]; then
        echo "ERROR: missing $FIXTURE_DIR/$f" >&2
        MISSING=1
      fi
    done
    if [[ $MISSING -ne 0 ]]; then
      echo "ERROR: fixtures missing" >&2
      exit 1
    fi
    # Validate deepseek-output.json against schema (requires Python 3.11+)
    if [[ -d "$REPO_ROOT/src" ]]; then
      PYTHON_BIN="$(bash "${SCRIPT_DIR}/find-python311.sh")"
      PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" -m akb2.briefing.validator \
        "$REPO_ROOT/$FIXTURE_DIR/deepseek-output.json"
    fi
    echo "dry-run fixtures present"
    exit 0
    ;;

  *)
    echo "Usage: $0 --collect | --synthesize | --check"
    exit 1
    ;;
esac
