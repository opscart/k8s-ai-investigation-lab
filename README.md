# Kubernetes AI Investigation Lab

An independent experiment: does a Microsoft Foundry agent produce more useful Kubernetes
diagnoses when it can retrieve application source and deployment configuration?

Start with direct repository retrieval. Add indexed RAG only if evaluation shows a
retrieval gap. Integrate the useful approach with OpsCart afterward.

## Status

Initial runnable foundation, **not a proven diagnosis product**. Includes:

- A standalone CLI with baseline, bounded context-pack, and Foundry prompt-agent modes.
- Captured incident evidence and optional operator-reviewed log excerpts.
- Up to three repository roles, each pinned to an immutable commit and explicit allowlist.
- JSON/Markdown reports with evidence references, usage and call counts.
- Synthetic probe-mismatch and insufficient-evidence cases with a human scoring rubric.
- Offline tests for tool boundaries, approval, provider wiring and result validation.

Live Azure validation requires your Foundry project. No live Kubernetes connector,
Azure DevOps API connector, indexed RAG, OpsCart API, or remediation is implemented yet.
Existing local clones from GitHub or Azure DevOps work with the repository tools.

## 1. Install locally

Python 3.11+ and Git are required. For cloud runs, install Azure CLI and sign in.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
pytest -q
```

The lock records versions used for local validation. For dependency development, use
`python -m pip install -e '.[dev]'` and review changes before refreshing the lock.

## 2. Preview offline first

Run from this repository's root after the initial commit has been pulled:

```bash
REVISION="$(git rev-parse HEAD)"
investigate preview --mode agent \
  --case evals/cases/probe-mismatch.json \
  --repo . --commit "$REVISION" --policy config/demo-policy.json
```

This prints the incident, repository inventory, exact allowed file contents and approval
fingerprint. It makes **no cloud request**. Reads use committed blobs, so uncommitted
edits do not change the source the agent sees. Evaluation answers are outside the allowlist.

## 3. Configure Foundry

Use a Foundry project with Agent Service access and a model deployment supporting
function calling and JSON output. A model-only Azure OpenAI endpoint is insufficient
for the agent mode. Use your actual deployment name, not an assumed model label.

```bash
az login
az account set --subscription YOUR_SUBSCRIPTION_ID
cp .env.example .env
# Edit .env with the PROJECT endpoint and actual model deployment name.
set -a
source .env
set +a
```

Azure CLI authentication is used explicitly, including when API keys are disabled.
Signing in does not grant project permissions; your identity needs access to create/use
and delete temporary agent versions and conversations in this project. Corporate
network and data-use rules still apply. Personal Azure experiments use synthetic data.

## 4. Run the baseline

```bash
investigate preview --mode baseline --case evals/cases/probe-mismatch.json
```

Review the output and copy its fingerprint into the following command:

```bash
investigate run --mode baseline --case evals/cases/probe-mismatch.json \
  --approve PASTE_BASELINE_FINGERPRINT --output .local/probe-baseline
```

The baseline uses the same deployment and diagnosis instructions but has no repository
tools or repository contents. It sees the supplied incident and reviewed logs only.

## 5. Run the Foundry agent

After configuring the endpoint, repeat the agent preview so approval includes the
destination and deployment:

```bash
investigate preview --mode agent \
  --case evals/cases/probe-mismatch.json \
  --repo . --commit "$REVISION" --policy config/demo-policy.json

investigate run --mode agent \
  --case evals/cases/probe-mismatch.json \
  --repo . --commit "$REVISION" --policy config/demo-policy.json \
  --approve PASTE_AGENT_FINGERPRINT --output .local/probe-agent
```

The runner creates a temporary Foundry prompt-agent version, executes requested local
functions, validates the response, and attempts to delete the conversation and agent
version. It does not host a public endpoint or give Foundry direct filesystem access.
Agent records or service traces may remain according to Azure configuration; cleanup is
not a guarantee of zero provider retention. Failed cleanup is reported explicitly.

Optional reviewed logs: add `--logs .local/reviewed-logs.txt` to both preview and run.
Edit that file yourself before approval. At most 12,000 bytes are accepted. This version
does not fetch logs or automatically sanitize them. Repository file approval is likewise
not a redaction guarantee. Changed content/destination/mode invalidates approval.

## 6. Compare actual results

```bash
python scripts/compare_results.py .local/probe-baseline/*.json .local/probe-agent/*.json
```

Score both answers with [the rubric](evals/expected/rubric.md). Repeat with
`evals/cases/insufficient-evidence.json`, new previews, and separate output directories.
The easy probe case may be solvable without retrieval; a tie is a valid finding.
Repeat runs before drawing conclusions. No benchmark win or real incident diagnosis
is claimed by the included fixtures or offline tests.

The configuration-mismatch case is designed to require repository context: its approved
logs do not reveal the expected environment-variable name. Preview and run it in both
modes, using `config/config-mismatch-policy.json` for agent mode and fresh output
directories such as `.local/config-baseline` and `.local/config-agent`. Compare whether
the baseline calibrates uncertainty and whether the agent cites both the application
source and deployed manifest before proposing the exact correction.

## 7. Compare bounded multi-repository context

Generate an ignored registry for the portable three-role fixture after committing or
pulling its files. A real registry may point each role at a different local clone and
immutable commit; repository contents are never fetched remotely by the model.

```bash
python scripts/create_fixture_repository_set.py --revision HEAD \
  --output .local/multi-repo-set.json

investigate preview --mode context-pack \
  --case evals/cases/multi-repo-config.json \
  --repository-set .local/multi-repo-set.json \
  --input-price-per-million YOUR_INPUT_PRICE \
  --output-price-per-million YOUR_OUTPUT_PRICE
```

The preview prints the exact ranked, bounded chunks that one model call will receive.
Run it with the displayed fingerprint and a fresh output directory, then repeat in
`agent` mode with the same registry. Agent mode exposes read-only search/file tools and
may make multiple calls; it does not preload every allowed file into the request.

```bash
investigate run --mode context-pack \
  --case evals/cases/multi-repo-config.json \
  --repository-set .local/multi-repo-set.json \
  --input-price-per-million YOUR_INPUT_PRICE \
  --output-price-per-million YOUR_OUTPUT_PRICE \
  --approve PASTE_CONTEXT_PACK_FINGERPRINT --output .local/multi-context

investigate preview --mode agent \
  --case evals/cases/multi-repo-config.json \
  --repository-set .local/multi-repo-set.json
```

Prices are operator-supplied USD per million tokens because Azure price, region, model,
and agreement can change. They affect the approval fingerprint and report only; the lab
does not claim that its estimate replaces Azure billing data. Compare all three modes:

```bash
python scripts/compare_results.py \
  .local/multi-baseline/*.json \
  .local/multi-context/*.json \
  .local/multi-agent/*.json
```

## Azure DevOps cross-repository experiment

The synthetic application repository in
[examples/azure-devops/pipeline-app-demo](examples/azure-devops/pipeline-app-demo)
references the synthetic shared pipeline library in
[examples/azure-devops/pipeline-shared-library-demo](examples/azure-devops/pipeline-shared-library-demo).
The first build is intentionally expected to fail: the application pipeline supplies a
different JVM property name from the one required by the Java source. The Maven command
lives in the shared job template.

Compare a baseline investigation using only the generic build failure log with a
repository-aware investigation that can read the application source, application
pipeline, and shared templates. The baseline should express uncertainty; identifying
the exact correction requires correlating those files. No corporate code or data is
included in this fixture.

From the application fixture directory, verify both outcomes locally:

```bash
mvn test -Dbilling.service.url=http://billing-api.invalid
mvn test -Dbilling.api.url=http://billing-api.invalid
```

The first command should fail with `startup dependency configuration invalid`; the
second should pass.

## Output

Reports contain assessment, likely cause, alternatives, proposed correction, verification,
missing evidence, source references, model/tool counts, token usage and elapsed time.
Evidence IDs are checked for existence, not semantic correctness. A human must verify
whether each cited source supports the conclusion. Reports can contain source/log details
quoted by the model; keep real reports private in the ignored `.local/` directory.

## Bounds and limitations

- Context-pack uses one model call and no tools. Agent allows at most 6 model calls and
  12 tool calls; no mode automatically retries.
- 120-second loop budget, checked between calls; individual requests capped at 45 seconds.
  SDK timeouts are network-operation timeouts, not hard process deadlines. Agent setup
  and cleanup are outside the investigation timer. Token acquisition has its own timeout.
- 3,500 output tokens per model call; 60,000-character input/evidence budget. This is
  not a guaranteed tokenizer-level total-context limit; provider history/reasoning adds overhead.
- One to three repositories, each with up to 100 explicit allowed files of 64 KiB each;
  regular UTF-8 Git blobs only. Context packs default to 20,000 characters / 12 chunks.
- At most 8 search hits; file reads at most 120 lines / 10,000 characters.
- No shell tool, cluster writes, automatic changes, indexing or background polling.
- Local approval is a CLI consent mechanism, not multi-user authorization.

## Development

```bash
ruff check .
ruff format --check .
pytest -q
git diff --check
```

See [architecture](docs/architecture.md), [roadmap](docs/roadmap.md),
[troubleshooting](docs/troubleshooting.md), and [optional Minikube reproduction](docs/minikube.md).
