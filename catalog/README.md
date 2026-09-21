# Catalog

Version 3 runtime data for **Computer Vision: Algorithms and Applications, 2nd Edition** by Richard Szeliski is committed here under `catalog/cvaa2e/`.

The source PDF itself is not committed. The exact reviewed source is identified by SHA-256:

```text
f3887dd48aa497ada733d730f69e884fe2fda3d988c4bb17fc47c4a492b7c4cf
```

The generation, review, validation, and reproducibility process is documented in `CATALOG_BUILD_WORKFLOW.md` at the repository root.

## Section Locator artifacts

```text
catalog/cvaa2e/manifest.yaml
catalog/cvaa2e/manifest.sections.01.yaml ... manifest.sections.15.yaml
catalog/cvaa2e/manifest.sections.A.yaml ... manifest.sections.C.yaml
catalog/cvaa2e/compiled_locator_index.json
catalog/cvaa2e/compiled_locator_index.sections.01.json ... compiled_locator_index.sections.15.json
catalog/cvaa2e/compiled_locator_index.sections.A.json ... compiled_locator_index.sections.C.json
catalog/cvaa2e/validation_report.json
```

`manifest.yaml` and `compiled_locator_index.json` are strict package manifests. The committed baseline contains 938 physical PDF pages, 351 printed section nodes, 242 project learning units, and 593 total section locators. Chapters 1-15 and Appendices A-C are represented as 18 root shards.

`validation_report.json` records package and shard hashes and reports:

- `source_pdf_verification_status: passed`
- `structural_validation_status: passed`
- `file_search_retrieval_status: not_tested`

The last status is intentionally not inferred from local PDF checks. It can only change after a real GPT file-search acceptance run.

## Exercise Locator artifacts

```text
catalog/cvaa2e/exercises.yaml
catalog/cvaa2e/exercises.sections.02.yaml ... exercises.sections.14.yaml
catalog/cvaa2e/compiled_exercise_index.json
catalog/cvaa2e/compiled_exercise_index.sections.02.json ... sections.14.json
catalog/cvaa2e/exercise_validation_report.json
```

The source contains formal `Exercises` sections in chapters 2-14. The committed baseline contains 172 exercises across 13 chapter shards, including 26 cross-page exercises and 217 explicit resolved references. All represented chapter exercise ranges are contiguous with no missing numbers.

The book contains a genuine mutual exercise reference between Exercises `10.9` and `13.2`. Both direct references are preserved as source facts. Aggregate execution plans stop at an already visited ancestor so the cycle does not cause recursive refetching.

The compiled exercise package contains 4,031 evidence-derived queries, with zero unbalanced queries and zero queries requiring further NFKC normalization. `exercise_validation_report.json` also reports source-PDF and structural validation as `passed`, while real GPT file-search retrieval remains `not_tested`.

## Runtime configuration

Deployments that enable both catalogs should use:

```text
TEACHING_GPT_LOCATOR_INDEX_PATH=./catalog/cvaa2e/compiled_locator_index.json
TEACHING_GPT_EXERCISE_INDEX_PATH=./catalog/cvaa2e/compiled_exercise_index.json
```

Missing, malformed, partial, book-mismatched, or internally inconsistent packages prevent startup.
