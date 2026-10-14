// Graph size. Each count(n) on a single label is answered from Neo4j's count
// store (no scan), so this stays fast whatever the graph size.
CALL () { MATCH (n:Model) RETURN count(n) AS models }
CALL () { MATCH (n:Unlisted) RETURN count(n) AS unlisted }
CALL () { MATCH (n:Organization) RETURN count(n) AS organizations }
CALL () { MATCH (n:License) RETURN count(n) AS licenses }
CALL () { MATCH (n:Dataset) RETURN count(n) AS datasets }
CALL () { MATCH (n:Paper) RETURN count(n) AS papers }
CALL () { MATCH ()-[r:FINETUNED_FROM]->() RETURN count(r) AS finetune }
CALL () { MATCH ()-[r:ADAPTER_OF]->() RETURN count(r) AS adapter }
CALL () { MATCH ()-[r:QUANTIZED_FROM]->() RETURN count(r) AS quantized }
CALL () { MATCH ()-[r:MERGED_FROM]->() RETURN count(r) AS merge }
RETURN models, unlisted, organizations, licenses, datasets, papers, finetune, adapter, quantized, merge
