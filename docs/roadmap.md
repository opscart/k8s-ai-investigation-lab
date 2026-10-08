# Experimental milestones

1. **Current foundation:** offline captured incidents, pinned direct repository retrieval,
   Foundry baseline/agent paths, approval and reports. Live Azure smoke test still required.
2. **Evidence of usefulness:** add a held-out configuration mismatch and dependency failure,
   run repeated matched trials, record manual scores, tokens, latency and unsupported claims.
   Keep grading answers unreachable by agent tools. No success claims from mocked tests.
3. **Live read-only evidence:** explicit context/namespace/workload policy; actual probes,
   termination timestamps, relevant events, config references; optional reviewed log capture.
   Keep secret values out. Verify the image-to-source mapping and Istio/container selection.
4. **Retrieval decision:** add indexed RAG only if the agent fails to find available relevant
   context. Wrong reasoning with correct context is a separate problem. Maintain comparable
   frozen inputs and identical repository versions across direct/indexed runs.
5. **OpsCart integration:** authenticated request/result API around the proven engine,
   cancellation/progress UX, reviewed evidence submission and operational deployment design.

Live Kubernetes, HTTP integration, Azure DevOps APIs, managed identity deployment and search
infrastructure are deliberately not placeholder implementations in this first milestone.
No reuse of the LLM recovery/inference repository is required.
