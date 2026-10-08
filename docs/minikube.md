# Optional synthetic reproduction

The captured case is sufficient for the first Foundry experiment. These commands are
optional and mutate only the explicit Minikube test namespace. The investigation agent
does not execute them. Do not substitute a corporate context for this example.

```bash
minikube image build -t investigation-demo:local examples/demo-app
kubectl --context minikube create namespace investigation-lab
kubectl --context minikube apply -f examples/demo-app/deployment.yaml
kubectl --context minikube -n investigation-lab get pods -w
```

The manifest intentionally points liveness at a path the app does not implement. Expect
probe failures and restarts; readiness can briefly be true between restarts. This is a
known test, not an unexplained production incident. Exit code and timing can differ from
the synthetic fixture. Capture actual observations rather than claiming the fixture is live.

```bash
kubectl --context minikube -n investigation-lab get events --sort-by=.metadata.creationTimestamp
kubectl --context minikube -n investigation-lab get pods -l app=inventory-demo
# Substitute the observed pod name; the container name is app.
kubectl --context minikube -n investigation-lab logs POD_NAME -c app --previous
```

Use a local copy of the incident JSON with reviewed actual evidence if evaluating the
live reproduction. To remove this dedicated test environment when finished:

```bash
kubectl --context minikube delete namespace investigation-lab
```
