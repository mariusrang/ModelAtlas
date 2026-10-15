# How to apply (one branch = one PR, as in the fiche)

In your clone of ModelAtlas (with your own git identity configured), for each task:

```bash
git checkout main && git pull
git checkout -b feat/license-rules
git apply /path/to/ModelAtlas_Ylias/1_license-rules.patch
git add config/licenses.toml docs/licenses.md
git commit -m "feat: add usage rules for the most common model licenses"
git push -u origin feat/license-rules

git checkout main
git checkout -b docs/anti-genericity
git apply /path/to/ModelAtlas_Ylias/2_anti-genericity.patch
git add docs/anti_genericity
git commit -m "docs: add anti-genericity appendix comparing our schema to AI-generated ones"
git push -u origin docs/anti-genericity

git checkout main
git checkout -b docs/graph-schema-diagram
git apply /path/to/ModelAtlas_Ylias/3_graph-schema-diagram.patch
git add docs/diagrams/graph_schema.drawio docs/diagrams/graph_schema.png README.md
git commit -m "docs: add Neo4j graph schema diagram (draw.io + png)"
git push -u origin docs/graph-schema-diagram
```

If `git apply` complains (main changed since the zip), copy the files from the matching `files_*` folder instead:
they are at the same paths as in the repo.

Then open each PR on GitHub, reviewer: mariusrang.
