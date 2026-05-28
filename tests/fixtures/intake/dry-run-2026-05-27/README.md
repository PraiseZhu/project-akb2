# dry-run-2026-05-27 Fixture

generated: 2026-05-27T07:30:00+08:00
source: synthetic (no X API credentials available at generation time)
date: 2026-05-27

## Files

- `sample.json` — 12 tweets, source: synthetic — no X API credentials
- `deepseek-output.json` — social_trends_v1_1 schema-valid, model: synthetic LLM output — no DeepSeek API key

## Data Provenance

Both files contain **synthetic data** generated to match real-world format and content quality.
Topic focus: AI/LLM frontier models, open-source releases, agentic coding workflows.
All tweet IDs, metrics, and timestamps are plausible but not real X.com data.

## Replay Commands

```bash
# Re-collect from X (requires X_BEARER_TOKEN or TWITTER_BEARER_TOKEN)
bash scripts/dry_run_e2e.sh --collect

# Re-run DeepSeek synthesis (requires DEEPSEEK_API_KEY or OPENROUTER_API_KEY)
bash scripts/dry_run_e2e.sh --synthesize

# Verify fixtures are present and deepseek-output passes schema
bash scripts/dry_run_e2e.sh --check
```

## Schema Validation

```bash
cd <worktree-root>
PYTHONPATH=src python -m akb2.briefing.validator \
  tests/fixtures/intake/dry-run-2026-05-27/deepseek-output.json
```

## Known Risks

- X API rate limits: 10 req/15 min on free tier; real collection may require backoff
- DeepSeek / OpenRouter API: may timeout on long contexts; set timeout >= 60s
- Synthetic tweets may not reflect actual trending topics on 2026-05-27
- deepseek-output.json uses model deepseek/deepseek-v4-pro via OpenRouter format
