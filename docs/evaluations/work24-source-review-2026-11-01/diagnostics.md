# Offline relevance review

Post-hoc diagnostics only; not a new live benchmark or replacement gold fixture. Unjudged pairs are not negatives. No corpus precision or expected-empty claim. Source hashes and excerpts are reviewer assertions: source bytes, factual relevance and redistribution permission are not verified by this tool.

```json
{
  "artifact_type": "offline_relevance_diagnostics",
  "binding": {
    "result_sha256": "109da31886fd783ac61ee2c0ccd31da8902dfe3427dbafbe4bda105b2d52bc5d",
    "evaluation_dataset": {
      "id": "work24-search-evaluation",
      "version": "1.0.0",
      "sha256": "597133d3c63833e7d7ab1d2e73648c0f7b2984db63726bed0f7c051ebae25ca5"
    },
    "snapshot": {
      "profile_id": "770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90",
      "dataset_id": "7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80",
      "revision": "5617a9f61b028005a4858fdac845db406aefb181",
      "manifest_sha256": "f4a79774ae2a470d7a9b80626cece532ad821900923069ba6875c9c9816cd88c",
      "snapshot_created_on": "2026-11-01"
    }
  },
  "packet_sha256": "58fc4c2f09895cac688e028e50254eabc92cd2f550721ebe4bffae1eb44e811f",
  "summary": {
    "returned_pairs": 600,
    "judged_pairs": 28,
    "relevant_pairs": 13,
    "explicit_negative_hits": 15,
    "unjudged_pairs": 572,
    "judgment_coverage": 0.04666666666666667,
    "successful_cases": 20,
    "failed_cases": 0
  },
  "at_k": {
    "1": {
      "returned_pairs": 20,
      "judged_pairs": 15,
      "relevant_pairs": 12,
      "explicit_negative_hits": 3,
      "unjudged_pairs": 5,
      "judgment_coverage": 0.75
    },
    "5": {
      "returned_pairs": 100,
      "judged_pairs": 28,
      "relevant_pairs": 13,
      "explicit_negative_hits": 15,
      "unjudged_pairs": 72,
      "judgment_coverage": 0.28
    },
    "10": {
      "returned_pairs": 200,
      "judged_pairs": 28,
      "relevant_pairs": 13,
      "explicit_negative_hits": 15,
      "unjudged_pairs": 172,
      "judgment_coverage": 0.14
    },
    "30": {
      "returned_pairs": 600,
      "judged_pairs": 28,
      "relevant_pairs": 13,
      "explicit_negative_hits": 15,
      "unjudged_pairs": 572,
      "judgment_coverage": 0.04666666666666667
    }
  }
}
```

Counts use distinct (case, course) pairs; repeated offerings do not add judgments.

| Case | Status | Pairs | Judged | Explicit negative hits | Unjudged | Duplicate rows |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| exact-title-network | ok | 30 | 1 | 0 | 29 | 0 |
| exact-title-semiconductor | ok | 30 | 1 | 0 | 29 | 0 |
| exact-title-automation | ok | 30 | 2 | 1 | 28 | 0 |
| exact-title-tax | ok | 30 | 3 | 2 | 27 | 0 |
| exact-title-labor | ok | 30 | 3 | 2 | 27 | 0 |
| exact-title-safety | ok | 30 | 2 | 1 | 28 | 0 |
| exact-title-security | ok | 30 | 1 | 0 | 29 | 0 |
| korean-network-paraphrase | ok | 30 | 1 | 0 | 29 | 0 |
| korean-semiconductor-paraphrase | ok | 30 | 1 | 0 | 29 | 0 |
| korean-labor-paraphrase | ok | 30 | 2 | 1 | 28 | 0 |
| mixed-network-terms | ok | 30 | 1 | 0 | 29 | 0 |
| mixed-semiconductor-terms | ok | 30 | 1 | 0 | 29 | 0 |
| mixed-automation-terms | ok | 30 | 2 | 1 | 28 | 0 |
| broad-career-goal | ok | 30 | 0 | 0 | 30 | 0 |
| broad-digital-goal | ok | 30 | 0 | 0 | 30 | 0 |
| similar-but-unreviewed-tax | ok | 30 | 1 | 1 | 29 | 0 |
| similar-but-unreviewed-security | ok | 30 | 0 | 0 | 30 | 0 |
| empty-result-candidate-astronomy | ok | 30 | 3 | 3 | 27 | 0 |
| empty-result-candidate-preservation | ok | 30 | 3 | 3 | 27 | 0 |
| unknown-personal-cost-and-timetable | ok | 30 | 0 | 0 | 30 | 0 |
