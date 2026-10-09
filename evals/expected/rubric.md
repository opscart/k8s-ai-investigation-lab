# Human evaluation — never expose this directory to agent retrieval

Score each category 0 (fails), 1 (partial), 2 (passes): correct diagnosis,
evidence support, concrete next action, calibrated uncertainty. Record latency,
input/output tokens and tool/model counts separately; a high tool count is not quality.

## probe-001

- Recognizes liveness HTTP 404 and explicit Killing event as restart evidence.
- Direct retrieval should locate /healthz in app.py and /health in deployment.yaml.
- Proposes changing the liveness path to /healthz and checking probe success/restarts.
- Does not infer OOM solely from exit 137 or recommend increasing memory blindly.
- Baseline may already solve this easy case from logs: record a tie honestly.

## unknown-001

- Does not assert OOM or a probe mismatch as established.
- Acknowledges unknown deployed revision and lack of time-correlated evidence.
- Requests termination/event details or historical memory data to distinguish causes.

## config-001

- Baseline does not invent a specific environment-variable name from the generic logs.
- Direct retrieval locates `INVENTORY_API_URL` in application source and
  `INVENTORY_SERVICE_URL` in the deployed manifest.
- Agent identifies the name mismatch as the supported startup failure cause.
- Proposes changing the manifest key to `INVENTORY_API_URL`, while preserving the value
  and avoiding disclosure of configuration values.
- Verification checks the rendered Deployment at the pinned revision, successful startup,
  and whether restart count remains stable during a new observation window.

This small fixture suite is a wiring and initial quality check, not proof of real-world
accuracy. Add held-out application/configuration failures as the experiment grows.
Review citations against actual content: ID existence validation alone is not entailment.
