# Troubleshooting

- **Repository has no commit:** commit/pull the initial foundation, then use `git rev-parse HEAD`.
  A branch name is intentionally rejected by --commit.
- **Allowed file missing:** check the policy against that commit, not the working tree.
  Symlinks, submodules, binary and oversized files are rejected.
- **Approval rejected:** repeat preview after changing inputs, logs, mode, source commit,
  Foundry endpoint or deployment. Copy the full displayed fingerprint.
- **Input schema error:** compare with evals/cases. Unknown fields are rejected. Evidence IDs
  must be unique and cannot start with repo_. Keep inputs under the documented bounds.
- **Foundry authentication:** run `az account show` and verify the intended tenant/subscription.
  A working Azure OpenAI deployment does not imply Agent Service project permissions.
- **Foundry operation failure:** confirm the project endpoint, deployed model's function/JSON
  support and permissions to use agents/conversations. This implementation follows the project
  API and does not silently fall back to classic APIs. Provider bodies are deliberately not
  printed because they can echo source/log content. Inspect Azure service status and safe
  project diagnostics. Do not paste tokens or full traces into public issues.
- **Budget/result rejection:** the provider must finish and return schema-valid JSON with
  supplied evidence IDs. A bounded failure is not reported as a successful diagnosis.
- **Output already exists:** choose a fresh --output directory for a repeat trial. Never
  overwrite earlier experiments to make a comparison look better.
- **Cleanup warning:** check the project for temporary `investigation-lab-*` versions and
  conversations from the run. Cleanup is best effort, not a retention policy.

Do not interpret passing local tests as proof that your Foundry project was exercised.
