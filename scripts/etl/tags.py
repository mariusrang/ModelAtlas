"""Parse the Hugging Face `tags` list into typed facts.

On the Hub, one flat `tags` list mixes six different concepts, for example:

    ["transformers", "safetensors", "llama", "text-generation", "en",
     "base_model:meta-llama/Llama-3.1-8B", "base_model:finetune:meta-llama/Llama-3.1-8B",
     "license:apache-2.0", "dataset:HuggingFaceH4/ultrachat_200k", "arxiv:2305.18290",
     "endpoints_compatible", "region:us"]

A list property like that can't be indexed or traversed well in Neo4j, so we
split it here into licenses, datasets, papers, parents (with the relation kind),
languages and "other" tags. This module is pure Python (no I/O) so it is easy
to unit-test, and both the profiler and the loader reuse it.
"""

import re
from dataclasses import dataclass, field

# The four relation kinds Hugging Face defines for `base_model_relation`.
RELATIONS = ("finetune", "adapter", "quantized", "merge")

# ISO 639-1 language codes (2 letters). We only accept these as languages, so
# tags like "en" count but "moe", "awq" or "lora" never do.
ISO_639_1 = frozenset(
    """aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz
    ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io
    is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr
    ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si
    sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh
    yi yo za zh zu""".split()
)

# Tags that are pure Hub plumbing: they say nothing about the model itself.
NOISE_TAGS = frozenset({"endpoints_compatible", "region:us", "region:eu", "autotrain_compatible", "has_space"})

# "7b", "0.5B", "1.5b", "70B", "135M", "8x7B" in a model id. The look-arounds stop
# us from matching inside words (e.g. "b" in "bert") or version strings.
_PARAM_RE = re.compile(r"(?<![a-z0-9.])(?:(\d+)x)?(\d+(?:\.\d+)?)([bm])(?![a-z0-9])", re.IGNORECASE)


@dataclass
class ParsedTags:
    """Everything we can read from one model's tag list."""

    licenses: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    papers: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    # (relation, parent_id); relation is None when the tag doesn't say it
    parents: list[tuple[str | None, str]] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    @property
    def license(self) -> str | None:
        """The declared license (a model declares at most one in practice)."""
        return self.licenses[0] if self.licenses else None


def _add_parent(parsed: ParsedTags, relation: str | None, parent: str) -> None:
    """Record a parent, upgrading an untyped entry when the typed tag shows up."""
    for i, (known_relation, known_parent) in enumerate(parsed.parents):
        if known_parent == parent:
            if known_relation is None and relation is not None:
                parsed.parents[i] = (relation, parent)
            return
    parsed.parents.append((relation, parent))


def parse_tags(tags: list[str] | None) -> ParsedTags:
    """Split a raw Hub tag list into typed groups.

    The Hub writes each parent twice: `base_model:<id>` and
    `base_model:<relation>:<id>`. We merge them into one (relation, id) pair.
    """
    parsed = ParsedTags()
    for raw in tags or []:
        tag = raw.strip()
        if not tag or tag in NOISE_TAGS:
            continue
        prefix, _, rest = tag.partition(":")
        if prefix == "license" and rest:
            parsed.licenses.append(rest.lower())
        elif prefix == "dataset" and rest:
            parsed.datasets.append(rest)
        elif prefix == "arxiv" and rest:
            parsed.papers.append(rest)
        elif prefix == "base_model" and rest:
            relation, _, parent = rest.partition(":")
            if relation in RELATIONS and parent:
                _add_parent(parsed, relation, parent)
            else:
                _add_parent(parsed, None, rest)
        elif tag.lower() in ISO_639_1:
            parsed.languages.append(tag.lower())
        else:
            parsed.other.append(tag)
    return parsed


def estimate_params_billions(model_id: str) -> float | None:
    """Guess the parameter count (in billions) from the model name.

    Hub metadata rarely gives the size, but names usually do: `Llama-3.1-8B`,
    `Qwen2.5-0.5B`, `SmolLM-135M`, `Mixtral-8x7B`. For mixture-of-experts names
    like `8x7B` we return experts x size, an upper bound (the real Mixtral is
    46.7B because experts share layers). The value is always stored with an
    `estimated` flag in the graph, never as a verified fact.
    """
    name = model_id.rsplit("/", 1)[-1]
    match = _PARAM_RE.search(name)
    if not match:
        return None
    experts, size, unit = match.groups()
    value = float(size) * (int(experts) if experts else 1)
    return value / 1000 if unit.lower() == "m" else value
