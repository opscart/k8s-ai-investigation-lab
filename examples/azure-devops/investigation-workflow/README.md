# Human-gated Azure DevOps investigation workflow (example POC)

`azure-pipelines.yml` investigates one explicitly supplied failed build. It only
orchestrates the existing commands; capture, approval fingerprints, the Foundry call and
evaluation are the same code as the local workflow. This is an example POC, **not a
production deployment**.

| Stage | Runs | Cloud access |
|-------|------|--------------|
| Capture | `investigate-ado capture`, `investigate preview --mode context-pack`, fingerprint + manifest, publish `investigation-review` | Azure DevOps read-only via `System.AccessToken`; **no model request** |
| Approval | agentless `ManualValidation@1` (server job); rejection or timeout stops the run | none |
| Analyze | download `investigation-review` (this run), re-preview, verify fingerprint, then `investigate run --mode context-pack` inside `AzureCLI@2`; publish `investigation-result` | Foundry via the service connection; 1 model request, 0 tools |
| Evaluate | `investigate-eval evaluate`; publish `investigation-evaluation` | none |

## Setup

1. **Pipeline.** Create a pipeline from `examples/azure-devops/investigation-workflow/azure-pipelines.yml`
   in this repository. There is deliberately no completion trigger; queue it manually.
2. **Foundry variable group.** Create a dedicated Azure DevOps Library variable group
   named `k8s-ai-investigation-foundry` containing only
   `AZURE_AI_PROJECT_ENDPOINT` (the Foundry project endpoint,
   `https://<resource>.services.ai.azure.com/api/projects/<project>`) and
   `AZURE_AI_MODEL_DEPLOYMENT_NAME`. Reference this group from the pipeline YAML and,
   under the variable group's **Pipeline permissions**, authorize only this investigation
   pipeline. Both values are part of the approval fingerprint, so Capture and Analyze must
   receive identical values. Do not reuse a broader variable group containing API keys or
   unrelated credentials.
3. **Azure service connection.** Create an Azure Resource Manager service connection whose
   identity can call the Foundry project and deployment (for example a Foundry/Azure AI user
   role on that project). Only the Analyze stage uses it; `AzureCLI@2` logs in so the existing
   `AzureCliCredential` path works unchanged. Restrict the connection to this pipeline and
   require its pipeline permission.
4. **Azure DevOps read access.** Capture maps `$(System.AccessToken)` to
   `AZURE_DEVOPS_EXT_PAT` in that one step only. The project's build service identity
   (`<Project> Build Service (<org>)`) needs, in the same project: **View builds** for the
   failed build's pipeline, and **Read** on the repositories named in the capture policy
   (and on the shared-library repository, if different). Both repositories and the failed
   build must be in this project; cross-project access needs a different credential and is
   not covered. Adjust the job authorization scope if your project restricts it.
5. **Capture policy.** The pipeline uses `config/azure-devops-build.example.json`; edit the
   organization, project, repository and step names for your pipeline before using it.

## Queue parameters

| Parameter | Meaning |
|-----------|---------|
| `failedBuildId` (required) | Failed build to investigate, e.g. `679` |
| `azureServiceConnection` (required) | Name of the Azure service connection for Analyze |
| `approvalReviewers` (required) | Users/groups notified to approve, semicolon-separated |
| `approvalTimeoutMinutes` | Approval wait; timeout rejects (default 240) |
| `evaluationCase` | Case file; blank selects `evals/matrix/azure-devops-build-679.json` for build 679 only, and skips evaluation for other builds |

## Review and approval

Open the `investigation-review` artifact of the running pipeline: `preview.txt` is the exact
bounded, sanitized evidence that will be sent, `capture.json` is the capture it came from,
and `fingerprint.txt` must equal the `Approval fingerprint` line in `preview.txt`. Resume
`ManualValidation` only to approve sending exactly that pack. The task approves the bounded
sanitized evidence pack; it offers **no** editable-log review, so reject and capture again if
anything is wrong. Sanitization is a conservative credential filter, not a guarantee.

Approval is tied to this run: Analyze downloads the artifact from the current run only, and
`workflow_guard.py verify` requires the manifest's run ID and failed build ID to match, every
file's SHA-256 to match the manifest, no extra files, and a freshly recomputed fingerprint to
equal the saved one. Any failure stops the stage before Foundry is contacted. The task does not
by itself prevent the person who queued the run from approving it; use environment approval
checks if you need separation of duties. Re-running a failed Capture stage fails rather than
overwriting the published artifact; queue a new run instead.

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

## Not verified offline

The YAML structure and guard script are tested offline. Task input names, variable-group
authorization, service-connection authorization, build-service permissions,
`ManualValidation` notification and the live Foundry call require a real Azure DevOps
organization and have not been exercised here.
