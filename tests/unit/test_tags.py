"""Unit tests for the Hub tag parser and the parameter estimator."""

import pytest

from scripts.etl.tags import estimate_params_billions, parse_tags

REAL_TAGS = [
    "transformers",
    "safetensors",
    "gemma4",
    "image-text-to-text",
    "unsloth",
    "en",
    "base_model:unsloth/gemma-4-E2B-it",
    "base_model:finetune:unsloth/gemma-4-E2B-it",
    "license:apache-2.0",
    "dataset:HuggingFaceH4/ultrachat_200k",
    "arxiv:2305.18290",
    "endpoints_compatible",
    "region:us",
]


def test_real_tag_list_is_split_correctly():
    parsed = parse_tags(REAL_TAGS)
    assert parsed.license == "apache-2.0"
    assert parsed.datasets == ["HuggingFaceH4/ultrachat_200k"]
    assert parsed.papers == ["2305.18290"]
    assert parsed.languages == ["en"]
    # the two base_model tags are merged into one typed parent
    assert parsed.parents == [("finetune", "unsloth/gemma-4-E2B-it")]
    assert "region:us" not in parsed.other and "endpoints_compatible" not in parsed.other
    assert set(parsed.other) == {"transformers", "safetensors", "gemma4", "image-text-to-text", "unsloth"}


def test_typed_tag_first_then_untyped_is_still_one_parent():
    parsed = parse_tags(["base_model:quantized:org/model", "base_model:org/model"])
    assert parsed.parents == [("quantized", "org/model")]


def test_merge_keeps_every_parent():
    parsed = parse_tags(["base_model:merge:a/one", "base_model:merge:b/two", "base_model:a/one", "base_model:b/two"])
    assert parsed.parents == [("merge", "a/one"), ("merge", "b/two")]


def test_untyped_parent_keeps_none_relation():
    assert parse_tags(["base_model:mistralai/Mistral-7B-v0.1"]).parents == [(None, "mistralai/Mistral-7B-v0.1")]


@pytest.mark.parametrize("tag", ["moe", "awq", "lora", "trl", "sft", "dpo", "xx"])
def test_short_tags_are_not_languages(tag):
    assert not parse_tags([tag]).languages


def test_empty_and_none_inputs():
    assert parse_tags(None).license is None
    assert parse_tags(["", "  ", "license:"]).other == ["license:"]


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("meta-llama/Llama-3.1-8B-Instruct", 8.0),
        ("Qwen/Qwen2.5-0.5B", 0.5),
        ("HuggingFaceTB/SmolLM-135M", 0.135),
        ("mistralai/Mixtral-8x7B-v0.1", 56.0),
        ("Qwen/Qwen3-30B-A3B-Instruct-2507", 30.0),
    ],
)
def test_param_estimate_from_name(model_id, expected):
    assert estimate_params_billions(model_id) == pytest.approx(expected)


@pytest.mark.parametrize("model_id", ["google-bert/bert-base-uncased", "someone/model-v2", "org/gpt2"])
def test_no_param_estimate_when_name_has_no_size(model_id):
    assert estimate_params_billions(model_id) is None
