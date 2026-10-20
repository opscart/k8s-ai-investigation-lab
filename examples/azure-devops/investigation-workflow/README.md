# Human-gated Azure DevOps investigation workflow (example POC)

The workflow is a reusable stage template,
[`templates/azure-devops/investigate-failed-build.yml`](../../../templates/azure-devops/investigate-failed-build.yml),
plus thin wrappers. It only orchestrates the existing commands; capture, approval
fingerprints, the Foundry call and evaluation are the same code as the local workflow.
This is an example POC, **not a production deployment**.

| Stage | Runs | Cloud access |
|-------|------|--------------|
| Capture | `investigate-ado capture`, `investigate preview --mode context-pack`, fingerprint + manifest, publish `investigation-review` | Azure DevOps read-only via `System.AccessToken`; **no model request** |
| Approval | agentless `ManualValidation@1` (server job) with a link to this run's artifacts; rejection or timeout stops the run | none |
| Analyze | download `investigation-review` (this run), re-preview, verify fingerprint, then `investigate run --mode context-pack` inside `AzureCLI@2`; publish `investigation-result` | Foundry via the service connection; 1 model request, 0 tools |
| Evaluate | `investigate-eval evaluate` when `evaluationCase` is set, otherwise a clear, successful skip; publish `investigation-evaluation` | none |

## Responsibilities

**Framework repository** (this repository, pinned by consumers): the template, the CLIs it
installs (`investigate`, `investigate-ado`, `investigate-eval`), `workflow_guard.py`, the
sanitizer, capture bounds and evaluation semantics. It contains no organization, project,
repository, model, endpoint or build-specific values.

**Consumer repository**: a small wrapper pipeline, its **capture policy** (organization,
project, repositories, step names, provenance labels, discovery entry pipeline), an optional
evaluation case, the Azure DevOps pipeline and its permissions, the Foundry variable group
and the Azure service connection.

**Policy ownership.** Service-specific policy and evaluation cases live in the consumer
repository. `capturePolicy` and `evaluationCase` are repo-relative paths in the consumer
repository (absolute paths and `..` are rejected). The framework only ships the generic
example `config/azure-devops-build.example.json` and the build-679 acceptance case in
`evals/matrix/`.

## Wrappers

- [`azure-pipelines.yml`](azure-pipelines.yml): this repository's own acceptance test
  (`frameworkRepository: self`). Create a pipeline from it and queue it manually; there is
  deliberately no completion trigger. For the build-679 acceptance run enter
  `evals/matrix/azure-devops-build-679.json` as `evaluationCase`.
- [`consumer-example.yml`](consumer-example.yml): a consuming repository declares the
  framework as a `repository` resource **pinned to a protected release tag or an immutable
  commit** (never `refs/heads/main`), invokes `template: ...@framework`, and passes the same
  alias as `frameworkRepository`. The template and the code that gets installed then come from
  the same pinned reference. The template checks out the framework explicitly into
  `$(Pipeline.Workspace)/framework`, installs from that directory only, and checks out the
  consumer into `consumer` for the policy and evaluation case.

```yaml
resources:
  repositories:
    - repository: framework
      type: github            # or git (Azure Repos)
      endpoint: <github-service-connection>
      name: opscart/k8s-ai-investigation-lab
      ref: refs/tags/v0.1.0   # protected tag or immutable commit
```

## Template parameters

| Parameter | Meaning |
|-----------|---------|
| `failedBuildId` (required) | Failed build to investigate |
| `frameworkRepository` | `self` (default) or the alias of the pinned framework repository resource |
| `capturePolicy` (required) | Repo-relative capture policy in the consumer repository |
| `azureServiceConnection` (required) | Azure service connection; used only by Analyze |
| `foundryVariableGroup` (required) | Variable group with the Foundry variables; used by Capture and Analyze |
| `approvalReviewers` (required) | Users/groups notified to approve, semicolon-separated |
| `approvalTimeoutMinutes` | Approval wait; timeout rejects (default 240) |
| `evaluationCase` | Repo-relative evaluation case in the consumer repository; blank skips evaluation |

## Prerequisites

1. **Foundry variable group.** A Library variable group containing only
   `AZURE_AI_PROJECT_ENDPOINT` (the Foundry project endpoint,
   `https://<resource>.services.ai.azure.com/api/projects/<project>`) and
   `AZURE_AI_MODEL_DEPLOYMENT_NAME`. Under **Pipeline permissions** authorize only the
   investigation pipeline. Both values are part of the approval fingerprint, so Capture and
   Analyze must receive identical values. Do not reuse a group holding API keys or unrelated
   credentials.
2. **Azure service connection.** An Azure Resource Manager service connection whose identity
   can call the Foundry project and deployment (for example a Foundry/Azure AI user role).
   Only the Analyze stage uses it; `AzureCLI@2` logs in so the existing `AzureCliCredential`
   path works unchanged. Restrict it to this pipeline and require its pipeline permission.
3. **Azure DevOps read access.** Capture maps `$(System.AccessToken)` to
   `AZURE_DEVOPS_EXT_PAT` in that one step only. The project's build service identity
   (`<Project> Build Service (<org>)`) needs, in the same project, **View builds** for the
   failed build's pipeline and **Read** on the repositories named in the capture policy. If
   the framework is an Azure Repos repository, it also needs Read there. Cross-project access
   needs a different credential and is not covered.
4. **Capture policy.** Adjust names to your pipeline; they must match exactly.

## Review and approval

The approval instructions link to this run's artifacts page. Open `investigation-review`:
`preview.txt` is the exact bounded, sanitized evidence that will be sent and `capture.json`
is the capture it came from. `ManualValidation` approves the bounded sanitized evidence pack;
it offers **no** editable-log review, so reject and capture again if anything is wrong.
Sanitization is a conservative credential filter, not a guarantee.

Approval is tied to this run: Analyze downloads the artifact from the current run only, and
`workflow_guard.py verify` requires the manifest's run ID and failed build ID to match, every
file's SHA-256 to match the manifest, no extra files, and a freshly recomputed fingerprint to
equal the saved one. Any failure stops the stage before Foundry is contacted.

## Evaluation outcome

Without a signed human review file, `investigate-eval` exits `3` (`human_review_required`):
the deterministic gates passed and semantic correctness is pending. The pipeline treats this as
the expected outcome, not a failed diagnosis. Exit `1` (failed gate) or `2` (input error) fails
the stage. The pipeline never supplies review answers; to record a review, download the
artifacts and follow [docs/evaluation.md](../../../docs/evaluation.md#recording-human-review).

## Retention and privacy

Artifacts contain sanitized build-log and source excerpts, the exact preview, and the model's
output. They are stored in Azure DevOps under your retention policy, readable by anyone who can
view the run. Limit run visibility, shorten retention for these runs, and delete artifacts you do
not need. Do not send real corporate content to a personal Foundry project. Nothing is written
to Git, and the workflow does not modify repositories, open pull requests, rerun builds, deploy
or change Azure/Kubernetes resources.

## Limitations

- The template cannot verify that a tag is immutable; protect the tag (or use a commit) in the
  framework repository.
- `ManualValidation` does not stop the person who queued the run from approving it; use
  environment approval checks if you need separation of duties.
- Re-running a failed Capture stage fails rather than overwriting the artifact; queue a new run.
- One failed build per run, Azure DevOps builds in the same project only, `context-pack` mode only.
- Verified offline: rendered YAML structure and the guard script. Not exercised without a real
  organization: task input names, template/resource resolution, checkout paths, variable-group
  and service-connection authorization, build-service permissions, `ManualValidation`
  notifications and the live Foundry call.
