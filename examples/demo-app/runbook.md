# Inventory demo operational notes

The service uses PORT, default 8080. Health endpoint implementation is in app.py.
Check the actual deployed probe paths and event messages when investigating restarts.
Successful startup is not proof of later health. Distinguish readiness failures from
liveness-triggered termination. Compare source at the deployed version, not latest main.
This application has no external database and does not need production credentials.
