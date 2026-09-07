# ACCB GPT-5.6 Sol provider triad

Owner-approved bounded comparison for Site Auditor issue #820.

- model: `openai/gpt-5.6-sol`
- logical fixture: same frozen 32K ACCB context for all lanes
- lanes: `openai`, `azure`, `amazon-bedrock`
- provider fallback: disabled
- automatic retries: 0
- storage: false
- raw request/model output retention: false
- transport: existing governed `OPENROUTER_SOCKS_URL`
- session hard ceiling: 300 RUB total / 100 RUB per lane
- purpose: distinguish provider-specific OpenRouter upstream behavior from shared transport/edge behavior

This is a diagnostic comparison, not a change to the frozen ACCB model slot or 512K anchor.