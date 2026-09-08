# ACCB Layer B final execution adjudication v0.1

Status: **ACCB_LAYER_B_DIAGNOSTIC_COMPLETE_WITH_ONE_INTEGRATION_EXCLUSION**

Canonical evidence ledger: Site Auditor issue `#798`.

## Canonical identities

- Experiment: `ACCB-LAYER-B-DIAGNOSTIC-v0.3`
- Scenario: `ACCB-DEV-004` v0.1
- Frozen Architecture/scorer SHA: `b47b937873ef980601b5c741af9b327fb18365bc`
- Execution admission SHA: `363f69971ed82ce3e4fc5ea9716652e57e83118e`
- Corrected 15-cell live run: `34182582503`
- Exact Site Auditor SHA for corrected live run: `d5eea309f89087c8f44caebf3e46ddc9015c1556`
- Single-cell recovery run: `34185036872`
- Exact Site Auditor SHA for recovery run: `af3ed3d64b230b703b1d8b029374b3a136cb135a`

## Primary comparison axis

Cross-model input size is the exact UTF-8 byte length of the identical request text.

| Tier | request_text_bytes | payload_sha256 |
|---|---:|---|
| small | 143934 | `e508100bf400d4248a97a7c4aa8a64d7e22db14d2325f98508ae60ab1218c32e` |
| medium | 575367 | `eb3bf2c81c12320ce992fd8aba0028e891673f7054d3bd2286ad58a486f811cc` |
| large | 2297725 | `048bc9e5d99f29498bac8b15314ac08eb0c18bcc326a7cf78a8c710053e82e38` |

Provider-reported token counts are retained only as model-specific diagnostic telemetry and are not the cross-model alignment axis.

## Final 5 × 3 matrix

| Model | 143934 B | 575367 B | 2297725 B |
|---|---:|---:|---:|
| z-ai/glm-5.2 | 0.850000 | 0.850000 | 0.850000 |
| deepseek/deepseek-v4-pro-0813 | 0.850000 | **N/A — integration exclusion** | 0.850000 |
| qwen/qwen3.7-plus | 0.766667 | 0.883333 | 0.850000 |
| moonshotai/kimi-k3 | 0.850000 | 0.850000 | 0.850000 |
| openai/gpt-5.6-sol | 0.850000 | 0.733333 | 0.966667 |

ACI_min observations:

| Model | 143934 B | 575367 B | 2297725 B |
|---|---:|---:|---:|
| z-ai/glm-5.2 | 0.5 | 0.5 | 0.5 |
| deepseek/deepseek-v4-pro-0813 | 0.5 | N/A | 0.5 |
| qwen/qwen3.7-plus | 0.5 | 0.5 | 0.5 |
| moonshotai/kimi-k3 | 0.5 | 0.5 | 0.5 |
| openai/gpt-5.6-sol | 0.5 | 0.0 | 0.8 |

## DeepSeek medium adjudication

The corrected 15-cell run produced 14 scored cells and one `HARNESS_FAILURE` at exactly:

- model: `deepseek/deepseek-v4-pro-0813`
- request size: `575367` bytes
- payload SHA256: `eb3bf2c81c12320ce992fd8aba0028e891673f7054d3bd2286ad58a486f811cc`

A separately governed single-cell recovery repeated only that cell, with no retries or fallbacks. It reproduced the same failure.

The recovery receipt records:

- error type: `ExecutionError`
- error-message SHA256: `f8acdbead434e1d9f673bf1c845afe67dce345b0a10e89bbd1dcd9e542dda99e`

That digest is exactly the SHA256 of the fixed harness message:

`RouterAI response content is empty`

Therefore the medium DeepSeek cell is adjudicated as:

`TERMINAL_INTEGRATION_EXCLUSION_EMPTY_FINAL_CONTENT`

It is **not** assigned ACI=0, and it is not treated as evidence of cognitive failure.

## Completion accounting

- Planned cells: 15
- Scored cognition cells: 14
- Terminal integration exclusions: 1
- Unscorable cognition outputs: 0
- Silent retries: 0
- Fallbacks: 0
- Tokenizer required at execution: false
- Corrected live-run accounted spend guard: `1529.740602 RUB`
- Recovery conservative cell guard: `32.229256 RUB`
- Recovery did not produce provider usage/cost telemetry before the empty-final-content exception, so no additional accounted provider cost is asserted here.

The diagnostic campaign is scientifically complete because every planned cell has one terminal disposition: a cognition score or a reproducible integration exclusion.

## Scientific boundary

This is a preregistered diagnostic calibration with one scored call per model/tier. It does not establish a universal context threshold, a universal model ranking, or a smooth causal response curve. The observed data do not support a monotonic decline of cognitive integrity with increasing payload size across the tested models.
