"""Synthetic service configuration used by the repository-grounding evaluation."""

import os

REQUIRED_BACKEND_VARIABLE = "INVENTORY_API_URL"


def load_backend_url(environment=None):
    values = os.environ if environment is None else environment
    backend_url = values.get(REQUIRED_BACKEND_VARIABLE, "").strip()
    if not backend_url:
        raise ValueError("startup configuration invalid")
    return backend_url


def main() -> int:
    try:
        load_backend_url()
    except ValueError as exc:
        # The captured fixture deliberately contains this generic operator-reviewed line.
        print(exc, flush=True)
        return 78
    print("service initialized", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
