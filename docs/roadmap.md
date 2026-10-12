# Experimental milestones

1. **Current foundation:** offline captured incidents, pinned direct repository retrieval,
   Foundry baseline/agent/context-pack paths, approval and reports. Live Azure smoke testing
   has validated baseline and agent modes; the Azure DevOps capture path still needs a live run.
2. **Evidence of usefulness:** add a held-out configuration mismatch and dependency failure,
   run repeated matched trials, record manual scores, tokens, latency and unsupported claims.
   Keep grading answers unreachable by agent tools. No success claims from mocked tests.
3. **Live read-only evidence:** the bounded Azure DevOps build collector is implemented for
   pipeline evidence. Next add explicit Kubernetes context/namespace/workload policy, actual
   probes, termination timestamps, relevant events and optional reviewed logs. Keep secret
   values out. Verify image-to-source mapping and Istio/container selection.
4. **Retrieval decision:** add indexed RAG only if the agent fails to find available relevant
   context. Wrong reasoning with correct context is a separate problem. Maintain comparable
   frozen inputs and identical repository versions across direct/indexed runs.
5. **OpsCart integration:** authenticated request/result API around the proven engine,
   cancellation/progress UX, reviewed evidence submission and operational deployment design.

Live Kubernetes, OpsCart HTTP integration, managed-identity deployment and search
infrastructure remain out of scope. Azure DevOps API capture is read-only and local; it does
not publish comments, rerun pipelines or modify repositories. No reuse of the LLM
recovery/inference repository is required.
