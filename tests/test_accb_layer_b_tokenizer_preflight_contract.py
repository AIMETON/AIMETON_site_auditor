from pathlib import Path


WORKFLOW = Path(".github/workflows/accb-layer-b-tokenizer-preflight.yml")
SCRIPT = Path("scripts/accb_layer_b_tokenizer_preflight.py")
PRODUCT_REQUIREMENTS = Path("requirements.txt")


def test_tokenizer_preflight_is_dispatch_only_and_zero_provider_spend() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "issue_comment:" not in trigger
    assert "inputs.expected_sha" in text
    assert "provider_generation_requests" in text
    assert "paid_spend_authorized_rub" in text
    assert "provider_api_secrets_used" in text
    assert "ROUTERAI_API_KEY" not in text
    assert "OPENROUTER_API_KEY" not in text
    assert "owner_spend_authorized" not in text
    assert "max_budget_rub" not in text
    assert "transformers==5.16.1" in text
    assert "huggingface-hub==1.30.0" in text
    assert "tiktoken==0.14.0" in text
    assert "b47b937873ef980601b5c741af9b327fb18365bc" in text


def test_tokenizer_dependencies_do_not_enter_product_runtime() -> None:
    requirements = PRODUCT_REQUIREMENTS.read_text(encoding="utf-8")
    assert "transformers" not in requirements
    assert "tiktoken" not in requirements
    assert "huggingface-hub" not in requirements
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"zai-org/GLM-5.2"' in script
    assert '"deepseek-ai/DeepSeek-V4-Pro-0813"' in script
    assert '"moonshotai/Kimi-K3"' in script
    assert '"o200k_base"' in script
