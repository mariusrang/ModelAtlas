"""A tiny in-memory stand-in for GraphRepository, used by the API tests.

Family used everywhere (child -> parent):
    acme/base  <-finetune-  acme/chat  <-quantized-  bob/chat-GGUF
    acme/base  <-adapter-   carl/lora
"""

MODELS = {
    "acme/base": {"id": "acme/base", "author": "acme", "downloads": 1000, "likes": 50, "task": "text-generation"},
    "acme/chat": {"id": "acme/chat", "author": "acme", "downloads": 500, "likes": 20, "task": "text-generation"},
    "bob/chat-GGUF": {"id": "bob/chat-GGUF", "author": "bob", "downloads": 300, "likes": 1, "task": "text-generation"},
    "carl/lora": {"id": "carl/lora", "author": "carl", "downloads": 10, "likes": 0, "task": "text-generation"},
}
APACHE = {"id": "apache-2.0", "family": "permissive", "commercial_use": True, "url": None, "notes": None}
NON_COMMERCIAL = {"id": "cc-by-nc-4.0", "family": "non-commercial", "commercial_use": False, "url": None, "notes": None}


def ancestor(model_id, path, rel_types, license_row=None, unlisted=False):
    """A row shaped like one item of license_check.cypher's `ancestors`."""
    return {
        "id": model_id,
        "unlisted": unlisted,
        "distance": len(rel_types),
        "path": path,
        "rel_types": rel_types,
        "license": license_row["id"] if license_row else None,
        "family": license_row["family"] if license_row else None,
        "commercial_use": license_row["commercial_use"] if license_row else None,
    }


class FakeRepository:
    """Same method names as GraphRepository; records every call."""

    def __init__(self):
        self.calls = []
        self.licenses = {"acme/base": APACHE, "acme/chat": APACHE, "bob/chat-GGUF": None, "carl/lora": NON_COMMERCIAL}

    def stats(self):
        return {
            "models": 4,
            "unlisted": 0,
            "organizations": 3,
            "licenses": 2,
            "datasets": 1,
            "papers": 0,
            "finetune": 1,
            "adapter": 1,
            "quantized": 1,
            "merge": 0,
        }

    def search(self, query, task, limit):
        self.calls.append(("search", query, task, limit))
        return [{"model": {**MODELS["acme/base"], "unlisted": False, "license": "apache-2.0"}, "rank": 4.2}]

    def model_detail(self, model_id):
        if model_id not in MODELS:
            return None
        parents = {
            "acme/chat": [{"id": "acme/base", "rel_type": "FINETUNED_FROM", "declared": True, "unlisted": False}]
        }
        return {
            "model": {**MODELS[model_id], "unlisted": False, "lineage_depth": 1, "root_ids": ["acme/base"]},
            "organization": MODELS[model_id]["author"],
            "license": self.licenses[model_id],
            "datasets": ["ds/one"],
            "papers": [],
            "languages": ["en"],
            "architectures": [],
            "parents": parents.get(model_id, []),
        }

    def lineage_up(self, model_id, depth, max_paths):
        self.calls.append(("lineage_up", model_id, depth, max_paths))
        if model_id not in MODELS:
            return None
        if model_id != "bob/chat-GGUF":
            return {"id": model_id, "paths": []}
        nodes = [
            {"id": i, "downloads": MODELS[i]["downloads"], "unlisted": False}
            for i in ("bob/chat-GGUF", "acme/chat", "acme/base")
        ]
        return {"id": model_id, "paths": [{"nodes": nodes, "rel_types": ["QUANTIZED_FROM", "FINETUNED_FROM"]}]}

    def descendants(self, model_id, depth, params):
        self.calls.append(("descendants", model_id, depth, params))
        if model_id != "acme/base":
            return []
        rows = [
            {"model": MODELS["acme/chat"], "distance": 1, "rel_type": "FINETUNED_FROM"},
            {"model": MODELS["bob/chat-GGUF"], "distance": 2, "rel_type": "QUANTIZED_FROM"},
            {"model": MODELS["carl/lora"], "distance": 1, "rel_type": "ADAPTER_OF"},
        ]
        if params["relation"]:
            rows = [r for r in rows if r["rel_type"] == params["relation"]]
        if params["after_downloads"] is not None:
            rows = [r for r in rows if r["model"]["downloads"] < params["after_downloads"]]
        return rows[: params["limit"]]

    def license_check(self, model_id, depth):
        self.calls.append(("license_check", model_id, depth))
        if model_id not in MODELS:
            return None
        ancestors = {
            "bob/chat-GGUF": [
                ancestor("acme/chat", ["bob/chat-GGUF", "acme/chat"], ["QUANTIZED_FROM"], APACHE),
                ancestor(
                    "acme/base",
                    ["bob/chat-GGUF", "acme/chat", "acme/base"],
                    ["QUANTIZED_FROM", "FINETUNED_FROM"],
                    APACHE,
                ),
            ],
            "carl/lora": [ancestor("acme/base", ["carl/lora", "acme/base"], ["ADAPTER_OF"], APACHE)],
        }
        return {"id": model_id, "license": self.licenses[model_id], "ancestors": ancestors.get(model_id, [])}

    def connection(self, model_a, model_b, depth):
        self.calls.append(("connection", model_a, model_b, depth))
        if model_a not in MODELS or model_b not in MODELS:
            return None
        return {
            "a": model_a,
            "b": model_b,
            "common": {"id": "acme/base", "downloads": 1000, "distance_from_a": 2, "distance_from_b": 1},
            "shortest_path": {
                "nodes": [model_a, "acme/chat", "acme/base", model_b],
                "rel_types": ["QUANTIZED_FROM", "FINETUNED_FROM", "ADAPTER_OF"],
            },
            "shared_datasets": ["ds/one"],
            "shared_papers": [],
            "same_organization": False,
        }

    def recommend_candidates(self, params):
        self.calls.append(("recommend_candidates", params))
        rows = [
            {"model": {**m, "license": (self.licenses[i] or {}).get("id")}, "languages": ["en"]}
            for i, m in MODELS.items()
            if m["task"] == params["task"]
        ]
        if params["commercial"]:
            rows = [
                r for r in rows if self.licenses[r["model"]["id"]] and self.licenses[r["model"]["id"]]["commercial_use"]
            ]
        return rows[: params["pool"]]

    def organization_footprint(self, org_id, depth, limit):
        self.calls.append(("organization_footprint", org_id, depth, limit))
        if org_id != "acme":
            return None
        return {
            "id": "acme",
            "own_models": 2,
            "own_downloads": 1500,
            "derived_models": 2,
            "builders": [{"organization": "bob", "models": 1}, {"organization": "carl", "models": 1}][:limit],
            "direct_children_by_kind": [{"rel_type": "ADAPTER_OF", "count": 1}],
        }

    def model_exists(self, model_id):
        return model_id in MODELS
