# Synthetic shared pipeline library

This directory represents a separate Azure Repos repository. The application pipeline
imports the CI stage template, which calls the Maven job template. The job accepts the
property name and value from the application pipeline and prints safe commit provenance
before running tests.
