"""Model names: tokens, base names and the candidate parents they suggest.

Most derived models keep their parent's name: `TheBloke/Mistral-7B-Instruct-v0.2-GGUF`
is a quantized copy of `mistralai/Mistral-7B-Instruct-v0.2`. So the cheapest way to
find possible parents is to look for older models that share rare name tokens.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

_SPLIT = re.compile(r"[-_.\s]+")
# Suffixes that say "this is a copy of something": format, quantization, bit width.
COPY_SUFFIXES = frozenset(
    """gguf ggml awq gptq exl2 exl3 mlx onnx openvino bnb int4 int8 fp8 fp16 bf16 4bit 8bit 3bit 2bit
    quantized quant imatrix imat i1 q2 q3 q4 q5 q6 q8 q2_k q3_k_m q4_k_m q4_0 q5_k_m q6_k q8_0""".split()
)


def name_of(model_id: str) -> str:
    """`org/Name-7B` -> `name-7b`."""
    return model_id.rsplit("/", 1)[-1].lower()


def tokens(model_id: str) -> list[str]:
    """Lower-case name tokens, without the organization."""
    return [t for t in _SPLIT.split(name_of(model_id)) if t]


def base_name(model_id: str) -> str:
    """Name without copy suffixes: `mistral-7b-instruct-v0.2-gguf` -> `mistral 7b instruct v0 2`."""
    kept = [t for t in tokens(model_id) if t not in COPY_SUFFIXES]
    return " ".join(kept or tokens(model_id))


@dataclass
class NameIndex:
    """Inverted index token -> models, with an idf weight per token."""

    ids: list[str]
    downloads: list[int]
    max_posting: int = 400
    idf: dict[str, float] = field(default_factory=dict)
    postings: dict[str, list[int]] = field(default_factory=dict)
    by_base: dict[str, list[int]] = field(default_factory=dict)
    token_sets: list[frozenset] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)

    def __post_init__(self):
        doc_freq: Counter = Counter()
        postings: defaultdict = defaultdict(list)
        by_base: defaultdict = defaultdict(list)
        self.token_sets = [frozenset(tokens(model_id)) for model_id in self.ids]
        self.bases = [base_name(model_id) for model_id in self.ids]
        for i, toks in enumerate(self.token_sets):
            doc_freq.update(toks)
            for tok in toks:
                postings[tok].append(i)
            by_base[self.bases[i]].append(i)
        n_docs = max(len(self.ids), 1)
        self.idf = {tok: math.log(n_docs / df) for tok, df in doc_freq.items()}
        # most downloaded first: for a very common token ("instruct", "7b") we only look at the
        # first `max_posting` ALLOWED models (see candidates), parents are usually popular
        self.postings = {tok: sorted(rows, key=lambda r: -self.downloads[r]) for tok, rows in postings.items()}
        self.by_base = dict(by_base)

    def name_score(self, child_row: int, cand_row: int) -> float:
        """Share of the child's name information (idf-weighted tokens) found in the candidate."""
        child_toks, cand_toks = self.token_sets[child_row], self.token_sets[cand_row]
        total = sum(self.idf.get(t, 0.0) for t in child_toks) or 1.0
        return sum(self.idf.get(t, 0.0) for t in child_toks & cand_toks) / total

    def candidates(self, child_row: int, allowed, limit: int = 50) -> list[int]:
        """Best `limit` candidate parents of a model, by shared rare tokens.

        `allowed(row)` filters candidates (e.g. created before the child). It is applied
        BEFORE the `max_posting` cap, so newer popular models can't push an older parent
        out of the list.
        A candidate with exactly the child's base name is always kept first.
        """
        scores: defaultdict = defaultdict(float)
        for tok in self.token_sets[child_row]:
            weight, kept = self.idf.get(tok, 0.0), 0
            for row in self.postings.get(tok, []):
                if row == child_row or not allowed(row):
                    continue
                scores[row] += weight
                kept += 1
                if kept >= self.max_posting:
                    break
        for row in self.by_base.get(self.bases[child_row], []):
            if row != child_row and allowed(row):
                scores[row] += 100.0  # same name once the copy suffixes are removed
        ranked = sorted(scores, key=lambda r: (-scores[r], -self.downloads[r]))
        return ranked[:limit]
