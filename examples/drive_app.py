"""Drive the UEFN Link app the way a user would, against a live fake editor.

Starts the real app server and the real fake UEFN server, then exercises the
full path: status, upload, launch, clear.
"""
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from http.server import ThreadingHTTPServer  # noqa: E402

from tests.fake_uefn import FakeUEFNServer  # noqa: E402
from tests.test_bridge import FakeBackend  # noqa: E402
import uefn_link_app as app  # noqa: E402


def post(url, payload=None, headers=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else b""
    )
    request = urllib.request.Request(url, data=data, method="POST")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode())


def main():
    import tempfile

    workspace = tempfile.mkdtemp(prefix="uefn-agent-")
    app.WORKSPACE_DIR = workspace
    app.UPLOAD_DIR = os.path.join(workspace, "uploads")
    os.makedirs(app.UPLOAD_DIR, exist_ok=True)

    with FakeUEFNServer() as editor, FakeBackend() as backend:
        app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
        app.BACKEND_URL = backend.url
        os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"

        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.2)

        print(f"app running at {base}\n")

        print("1. Status")
        status = get(f"{base}/api/status")
        print(f"   uefn    : {status['uefn']['detail']}")
        print(f"   backend : {backend.url} -> {status['backend']['detail']}")
        print(f"   workspace: {status['workspace']}\n")

        print("2. Upload assets")
        for name, size in [("T_Rock_Albedo.png", 4096), ("level_concept.jpg", 2048),
                           ("mechanics_spec.md", 512)]:
            result = post(f"{base}/api/upload", raw=b"x" * size,
                          headers={"X-Filename": name})
            print(f"   {result['saved']:<24} {result['bytes']:>6} bytes")
        print()

        print("3. Uploads now staged")
        for item in get(f"{base}/api/status")["uploads"]:
            print(f"   {item['name']:<24} {item['bytes']:>6} bytes")
        print()

        print("4. Send an instruction")
        result = post(f"{base}/api/start", {
            "task": "Swap the rock textures for the uploaded art, keep material "
                    "references, compile the Verse, and report the build result.",
        })
        print(f"   ok              : {result['ok']}")
        print(f"   conversation    : {result['id']}")
        print(f"   open at         : {result['url']}")
        print(f"   uploads passed  : {result['uploads_used']}")
        print()

        print("5. Clear uploads")
        print(f"   removed: {post(f'{base}/api/clear')['removed']}")

        server.shutdown()
        server.server_close()
        os.environ.pop("LOCAL_BACKEND_API_KEY", None)

    print("\nFull round trip works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
