"""Synthetic application source owned by the application repository."""

import os

REQUIRED_DEPENDENCY_VARIABLE = "BILLING_API_URL"


def load_dependency_url(environment=None):
    values = os.environ if environment is None else environment
    dependency_url = values.get(REQUIRED_DEPENDENCY_VARIABLE, "").strip()
    if not dependency_url:
        raise ValueError("startup dependency configuration invalid")
    return dependency_url
