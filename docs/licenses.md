# License rules

The license check (`GET /models/{id}/license-check`) answers one question: *can a company use this model
commercially?* The facts (which license each model declares) come from the model cards. The rules (what each
license allows) come from [`config/licenses.toml`](../config/licenses.toml), which we wrote by hand and explain here.

## Why a license is a node and not a string

We could store `license = "llama3.1"` as a property on every model. We made it a `:License` node instead:

- **Rules are written once.** `family`, `commercial_use` and `url` live on 78 `License` nodes, and 115,244
  `HAS_LICENSE` relationships point to them (reports/load_report.md). Fixing a rule means editing one line in the
  TOML file and re-running `make seed`, not updating thousands of models.
- **The check becomes a graph traversal.** A fine-tune can't drop the restrictions of the model it comes from, so
  we need the license of *every ancestor*. With nodes this is one pattern: walk up
  `FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM` and follow `HAS_LICENSE` from each ancestor
  ([`license_check.cypher`](../app/queries/license_check.cypher)). The verdict is the **most restrictive** status
  found on the way.
- **Missing licenses can be recovered.** 46.64% of the raw models declare no license (reports/data_profile.md).
  When a model has none, we judge it on the closest licensed ancestor (`inherited_from` in the answer). If nobody
  on the path declares one, the answer is `needs_review`.

## Training data

The check also follows `TRAINED_ON` from the model and its ancestors to each dataset's `HAS_LICENSE` links.
If any of these datasets has a non-commercial or conditional license, the verdict becomes at least `needs_review`.
This training data rule alone does not make the verdict `not_allowed`.
The legal effect of a training dataset's license on a model is still unclear, so a person must review it.
If the model's own license or an ancestor's license already gives `not_allowed`, that stricter verdict stays.

## The families

| family | meaning | example |
| --- | --- | --- |
| `permissive` | Do almost anything, keep the copyright notice. | `apache-2.0` |
| `copyleft` | Commercial use is fine, but what you share must stay under the same license. | `agpl-3.0` |
| `open-rail` | Commercial use is fine, but a list of forbidden uses applies and must be passed on to derivatives. | `openrail++` (SDXL) |
| `community` | A vendor license with conditions: user caps, use policy, naming rules. | `llama3.1`: OK under 700M monthly active users |
| `non-commercial` | No commercial use at all (CC-NC and research-only licenses). | `cc-by-nc-4.0` |
| `other` | `license: other` = a custom license, plus 2 licenses that fit no family (`cc-by-nd-4.0`, `c-uda`). | `other` |
| `unknown` | We have no reliable rule; a human must check. | `cc` (any Creative Commons license) |

`commercial_use` gives the status used by the check: `true` → `ok`, `"conditional"` → `conditional` (the
condition is in `notes`), `false` → `not_allowed`, no rule → `needs_review`.

## The most used licenses

Counts are the number of models in the raw dump of 861,627 models (`reports/data_profile.md`). In the graph the
counts are smaller because of our scope rules; to get them, run
`MATCH (l:License)<-[:HAS_LICENSE]-(m:Model) RETURN l.id, count(m) ORDER BY count(m) DESC` in Neo4j Browser.

| license id | family | commercial use | models (raw dump) |
| --- | --- | --- | --- |
| apache-2.0 | permissive | yes | 264,028 |
| mit | permissive | yes | 79,444 |
| other | other | needs review | 43,616 |
| openrail++ | open-rail | conditional | 10,782 |
| cc-by-4.0 | permissive | yes | 9,926 |
| cc-by-nc-4.0 | non-commercial | no | 8,147 |
| gemma | community | conditional | 7,477 |
| creativeml-openrail-m | open-rail | conditional | 4,971 |
| bsd-3-clause | permissive | yes | 3,712 |
| llama3.1 | community | conditional | 3,155 |
| llama3.2 | community | conditional | 2,878 |
| cc-by-nc-sa-4.0 | non-commercial | no | 2,485 |
| llama3 | community | conditional | 2,452 |
| agpl-3.0 | copyleft | yes | 1,978 |
| cc-by-sa-4.0 | copyleft | yes | 1,510 |
| bigscience-bloom-rail-1.0 | open-rail | conditional | 1,258 |
| llama2 | community | conditional | 1,102 |
| bigscience-openrail-m | open-rail | conditional | 1,102 |
| bigcode-openrail-m | open-rail | conditional | 1,085 |
| openrail | open-rail | conditional | 1,036 |

The TOML file also has a rule for the 62 other license ids that Hugging Face accepts in a card (every other
Llama, Creative Commons, GPL, BSD... version). Only the id `unknown` is left without a rule, on purpose: it stays
`family = "unknown"` and the check answers `needs_review`. Qwen and Mistral have no id of their own on Hugging Face:
their custom licenses (e.g. the Qwen research license) are declared as `other`.

## Limits

- This is a **simplified reading** of each license to sort models quickly. It is **not legal advice**.
- A `community` or `open-rail` license also forbids some *uses* (weapons, surveillance...). We can't check how a
  client will use a model, so these licenses are never `ok`, only `conditional`.
- `other` licenses are custom texts: a human must read the model card. Same for `cc` (could be non-commercial).
- Licenses are self-declared by uploaders and can be wrong, for example an `apache-2.0` fine-tune of a Llama
  model. That is exactly why the check also reads every ancestor.
