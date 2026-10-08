"""Small synthetic service. Run with python app.py; no third-party packages."""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"healthy\n")
        elif self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"demo inventory service\n")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found\n")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    print(f"Application listening on port {port}", flush=True)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
