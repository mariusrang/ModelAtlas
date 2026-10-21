# ER diagram of the source data

Our data comes as two Parquet tables, not as a graph: one row per **model card**
(`librarian-bots/model_cards_with_metadata`) and one row per **dataset card**
(`librarian-bots/dataset_cards_with_metadata`). Most of the relations are hidden in a
`tags` list column (`license:apache-2.0`, `base_model:finetune:org/name`, `dataset:org/name`,
`arxiv:2305.18290`, `en`...). The ETL ([`scripts/etl/tags.py`](../scripts/etl/tags.py))
splits those tags into the entities and link tables below. This is the relational
model we start from; [`modeling.md`](modeling.md) explains how it becomes the graph.

```mermaid
erDiagram
    MODEL_CARD {
        string modelId PK "org/name"
        string author FK
        int downloads
        int likes
        string library_name
        string pipeline_tag FK "task"
        datetime createdAt
        datetime last_modified
        list tags "parsed by the ETL"
        string card "text, only its length is kept"
    }
    DATASET_CARD {
        string datasetId PK
        string author
        int downloads
        int likes
        datetime createdAt
        list tags "license:, language:, size_categories:, task_categories:"
    }
    ORGANIZATION { string id PK }
    LICENSE { string id PK "family, commercial_use from config/licenses.toml" }
    PAPER { string arxiv_id PK }
    LANGUAGE { string code PK "ISO 639-1" }
    TASK { string id PK }
    MODEL_PARENT {
        string child FK
        string parent FK "may be missing from the dump"
        string relation "finetune, adapter, quantized, merge"
    }
    MODEL_DATASET { string model FK
        string dataset FK }
    MODEL_PAPER { string model FK
        string arxiv_id FK }
    MODEL_LANGUAGE { string model FK
        string language FK }
    MODEL_LICENSE { string model FK
        string license FK }
    ARCHITECTURE { string id PK }
    MODEL_ARCHITECTURE { string model FK
        string architecture FK }
    DATASET_LICENSE { string dataset FK
        string license FK }
    DATASET_LANGUAGE { string dataset FK
        string language FK }

    ORGANIZATION ||--o{ MODEL_CARD : publishes
    TASK ||--o{ MODEL_CARD : "pipeline_tag"
    MODEL_CARD ||--o{ MODEL_PARENT : child
    MODEL_CARD |o--o{ MODEL_PARENT : parent
    MODEL_CARD ||--o{ MODEL_DATASET : ""
    DATASET_CARD |o--o{ MODEL_DATASET : "cited (61% have a card)"
    MODEL_CARD ||--o{ MODEL_PAPER : ""
    PAPER ||--o{ MODEL_PAPER : ""
    MODEL_CARD ||--o{ MODEL_LANGUAGE : ""
    LANGUAGE ||--o{ MODEL_LANGUAGE : ""
    MODEL_CARD ||--o| MODEL_LICENSE : ""
    LICENSE ||--o{ MODEL_LICENSE : ""
    DATASET_CARD ||--o{ DATASET_LICENSE : ""
    LICENSE ||--o{ DATASET_LICENSE : ""
    DATASET_CARD ||--o{ DATASET_LANGUAGE : ""
    LANGUAGE ||--o{ DATASET_LANGUAGE : ""
    MODEL_CARD ||--o{ MODEL_ARCHITECTURE : ""
    ARCHITECTURE ||--o{ MODEL_ARCHITECTURE : ""
```

## From tables to the graph

| Relational side | Graph side | Why it changed |
| --- | --- | --- |
| `MODEL_CARD` row | `(:Model)` node | one node per model, the scalar columns become properties |
| `MODEL_PARENT` (self-join on `MODEL_CARD`, with a `relation` column) | 4 relationship types `FINETUNED_FROM`, `ADAPTER_OF`, `QUANTIZED_FROM`, `MERGED_FROM` | a family tree is a recursive self-join in SQL (one join per generation); in Neo4j it is one bounded variable-length pattern, and the type filters the expansion |
| `MODEL_PARENT.parent` with no `MODEL_CARD` row (22% of the edges) | `(:Model:Unlisted)` node | the foreign key would be broken in SQL; in the graph we keep the node so the tree and the license check don't stop there |
| `author` column | `(:Organization)` + `PUBLISHED_BY` | "who builds on X" groups by publisher without a full scan |
| `MODEL_LICENSE`, `DATASET_LICENSE` | `(:License)` + `HAS_LICENSE` from models **and** datasets | one place for the license rules, shared by models and their training data |
| one license per model, several per dataset | a model has zero or one license, a dataset zero or many | the Hub writes one `license:` tag per model in practice (none of the 2,001 models of our test sample has two), while 58 datasets declare several, so only datasets keep the full list. If a model had several, the ETL keeps the first one (`ParsedTags.license`) and the license check would miss the others: a known limit |
| `MODEL_DATASET` | `TRAINED_ON` | link table becomes a relationship |
| `MODEL_DATASET.dataset` with no `DATASET_CARD` row | `(:Dataset {listed: false})` | cited but no card: license unknown, see modeling.md §7 |
| `MODEL_PAPER`, `MODEL_LANGUAGE`, `DATASET_LANGUAGE` | `CITES`, `SUPPORTS` | link tables become relationships |
| `pipeline_tag` | `(:Task)` + `PERFORMS`, also copied as `Model.task` (range index) | the node groups models by task; the copy makes the frequent `task = ...` filter an index seek |
| architecture tags (`llama`, `mistral`...) in `tags` | `(:Architecture)` + `HAS_ARCHITECTURE` | same idea as tasks, only for the tags we know (`config/scope.toml`) |
| `DATASET_CARD.tags` (size, task categories) | `Dataset.size_category`, `Dataset.task_categories` properties | shown, never traversed |

The full property list of the graph is in [`docs/diagrams/graph_schema.png`](diagrams/graph_schema.png)
and in the constraints and indexes of [`app/database/schema.py`](../app/database/schema.py).
