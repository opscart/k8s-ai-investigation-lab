# Architecture and evidence boundaries

The CLI owns repository access and review. Foundry owns reasoning. Agent-mode function
requests are suggestions: local dispatch enforces the allowlist. Context-pack mode instead
retrieves everything deterministically before one model call.

```mermaid
flowchart TD
  A[Operator reviews incident and source scope] --> B[Local runner]
  B --> C[Foundry model or prompt agent]
  C --> D{Function requested?}
  D -->|Yes| E[Validate tool and read pinned Git blobs]
  E -->|Evidence IDs and source excerpts| C
  D -->|No| F[Validate diagnosis and citations]
  F --> G[Local report and human evaluation]
```

For Azure DevOps build investigations, a separate read-only capture command retrieves one
build record, its timeline, a named failed log, a named provenance log, and explicitly
allowlisted files at the resolved commits. The model never receives Azure DevOps credentials
or calls Azure DevOps tools. The resulting local artifact is previewed and fingerprinted
before context-pack mode sends it to Foundry.

## Evidence provenance

Captured incident evidence carries IDs and source labels. The synthetic fixtures explicitly
identify themselves. Real exports should include timestamps, workload/container identity,
image digest, deployed source commit, and actual configuration. Never relabel synthetic
or candidate source as verified live evidence.

Repository retrieval supplies repository ID, commit, path and line range. Local-clone mode
requires the operator to establish the revision mapping. Azure DevOps capture resolves the
application revision from `Build.SourceVersion` and additional repository revisions only
from configured, unambiguous provenance-log labels.
Files are read using Git object IDs, not working-tree paths or model-supplied shell text.
Git fetching and authentication remain outside model execution. Existing clones can be
updated manually, then a specific commit selected for each investigation.

Azure DevOps capture uses a PAT only when `AZURE_DEVOPS_EXT_PAT` is explicitly present;
otherwise it uses the current Azure CLI identity. Authentication headers are never stored
in the capture. The selected failed log is bounded but not automatically sanitized.

## Foundry adapter

Uses AzureCliCredential, AIProjectClient, prompt agent versions and the project Responses
client. Baseline bypasses the agent and has no tools. Both use the same model deployment,
incident evidence and diagnosis instructions. Each run has a fresh conversation.

The adapter targets the current project API, not classic hub connection-string APIs.
SDK/API availability and model support must be smoke-tested in the target project.
No API keys or corporate identifiers are embedded in the source.

Reference used for implementation:
https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/function-calling

## Privacy

Preview is entirely local. Explicit approval authorizes the exact incident and the complete
allowed file set for possible retrieval, destination and model. Only requested excerpts
are returned through tools. Repo content and logs may contain sensitive data: no automatic
redaction guarantee is made. Model output can quote that content.

Do not enable SDK HTTP/body or content tracing for sensitive trials without reviewing the
destination and retention policy. Cloud conversations are cleaned up on normal exits;
interrupts, network failures or process death can leave resources. Project monitoring
and billing records have their own retention. .gitignore is not a data-loss-prevention system.

## Future OpsCart adapter

Add an authenticated API accepting the common Incident contract plus an operator-approved
workload mapping. OpsCart can supply captured evidence without changing the engine.
Do not expose arbitrary repository paths, cluster selectors or credentials through that API.
The standalone CLI remains usable. No OpsCart files are changed by this repository.

## Future indexed retrieval

Extract a retriever protocol once a second implementation is justified. Both direct and
indexed retrieval must return the same Evidence objects. Index only approved files and
retain commit/path/line provenance. Apply workload, revision and access filters before
retrieval. Do not index evaluation answers or real incident logs by default.

Azure AI Search is a candidate, not a prerequisite:
https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search
