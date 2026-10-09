# Configuration demo operational notes

The service validates required runtime configuration before starting its server. Compare
the application source at the deployed commit with the environment names in the rendered
Deployment. Treat generic startup output as a symptom; it does not identify the missing
setting by itself. Do not print configuration values because they may contain credentials.
