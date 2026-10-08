import importlib.util
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


def test_demo_reproduces_route_mismatch():
    path = Path(__file__).parents[1] / "examples/demo-app/app.py"
    spec = importlib.util.spec_from_file_location("demo_app", path)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    with HTTPServer(("127.0.0.1", 0), app.Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/healthz", timeout=2) as result:
                assert result.status == 200
            with pytest.raises(HTTPError) as failure:
                urlopen(base + "/health", timeout=2)
            assert failure.value.code == 404
        finally:
            server.shutdown()
            thread.join(timeout=2)
