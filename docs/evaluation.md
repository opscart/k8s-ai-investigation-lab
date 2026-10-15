# Evaluation matrix (first increment)

`investigate-eval evaluate` scores a **saved** result and its capture against a declarative
case in `evals/matrix/`. It makes no network, Azure or model request and has no LLM judge.

```bash
investigate-eval evaluate \
  --case evals/matrix/azure-devops-build-679.json \
  --capture .local/build-679-discovered-final.json \
  --result .local/build-679-discovered-result/context-pack-<fingerprint>.json \
  --output .local/evaluations/build-679 \
  [--review .local/reviews/build-679.json]
```

Writes `evaluation.json` and `evaluation.md` (never overwrites). Output is deterministic:
no timestamps; input SHA-256 digests are included. Exit codes: 0 pass, 1 fail,
3 human review required, 2 input error. Captures and results stay in ignored `.local/`.

## Objectively checked (hard gates)

- **Consistency:** case ID equal in spec, capture and result; mode; the result's evidence
  set equals the capture's evidence set.
- **Provenance:** exact expected commit per repository role in the capture, the result's
  repository inventory and every excerpt source; no unexpected roles; no mutable-branch
  markers (`refs/heads/`, `@main`, `versionType=branch`); every repository excerpt source parses.
- **Evidence:** required evidence IDs; required repository/path references among excerpts;
  expected excerpt count.
- **Safety:** capture `safety.residual_findings == 0` (and summary present); context-selection
  summary present; no text that the local sanitizer would still change (capture or diagnosis).
- **Budgets:** model calls, tool calls, input/output tokens, retrieved chars/items.
- **Behavior:** `cause_status` matches the declared disposition; required claims
  (all regexes present in assessment/likely cause); forbidden claims (regex per sentence, ignored
  when the sentence contains negation); the correction does not claim it was applied.

Required/forbidden claim gates are **lexical**; their observed/expected cells list the required, matched, missing and forbidden patterns. They can pass for a wrong diagnosis (for
example correct words in a wrong argument) and negation handling is approximate.

## Textual heuristics (reported, not gating)

Each heuristic is `matched`, `NOT matched`, or `not_applicable`. `reports_missing_evidence`
is `not_applicable` for cases that expect a supported cause (no missing evidence expected).
Correction mentions/avoids declared strings (for build 679: mentions `propertyName` and
`billing.api.url`, avoids `propertyValue`), cited repository roles, and whether missing evidence
was reported.

## Requires human judgment

Semantic correctness, whether citations entail claims, correction minimality and calibration.
The report shows these as `pending`; status is `human_review_required` until a reviewer
records answers (see "Recording human review"). Only a recorded reviewer can produce `pass`.

## Recording human review

A review is a JSON file passed with `--review`. Schema (all fields required except `notes`;
unknown fields are rejected):

```json
{
  "reviewer": "string, 1-100 chars",
  "digests": {
    "case": "64 lowercase hex SHA-256 of the case file bytes",
    "capture": "64 lowercase hex SHA-256 of the capture file bytes",
    "result": "64 lowercase hex SHA-256 of the result file bytes"
  },
  "answers": {"<question id from the case>": "pass | fail"},
  "notes": "optional string, up to 2000 chars"
}
```

Complete build-679 example. Compute the digests of the exact files you reviewed
(`shasum -a 256 FILE`, macOS; `sha256sum FILE` on Linux). They are also printed under
"Input digests" in every evaluation report.

```json
{
  "reviewer": "your-name",
  "digests": {
    "case": "<sha256 of evals/matrix/azure-devops-build-679.json>",
    "capture": "<sha256 of .local/build-679-discovered-final.json>",
    "result": "<sha256 of .local/build-679-discovered-result/context-pack-FINGERPRINT.json>"
  },
  "answers": {
    "semantic_correctness": "pass",
    "citation_entailment": "pass",
    "correction_minimality": "pass",
    "calibration": "pass"
  },
  "notes": "Read each cited excerpt; correction changes only propertyName."
}
```

Command (still offline; no model or network request):

```bash
investigate-eval evaluate \
  --case evals/matrix/azure-devops-build-679.json \
  --capture .local/build-679-discovered-final.json \
  --result .local/build-679-discovered-result/context-pack-FINGERPRINT.json \
  --review .local/reviews/build-679.json \
  --output .local/evaluations/build-679-reviewed
```

Answers: `pass` means the reviewer judged the question satisfied, `fail` means it was not
(overall status becomes `fail`), and a question with **no** answer is `pending`
(status `human_review_required` while all hard gates pass). Answering `pass` to every
question with all hard gates passing gives overall `pass`. Answers for question IDs not in the
case are an input error.

**Binding:** every review carries the SHA-256 of the case, capture and result bytes. If any of
the three differs from the files being evaluated, evaluation stops with exit code 2 naming
what changed (for example `changed: capture`), writes no report, and the review must be
re-recorded. A review therefore cannot be carried over to a different or edited run.
The digests prove the review refers to those exact bytes; they do not prove the reviewer
actually read them.

## Limits

One case so far; budgets in the spec are declared caps, not measured baselines. Passing gates
does not show the model is reliable, and no accuracy claim is made from one run.
