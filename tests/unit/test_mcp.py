import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hippocampus import mcp_server

CALLS = []
AUTH = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append((self.path, body))
        AUTH.append(self.headers.get("Authorization"))
        if self.path == "/v1/search":
            out = {"results": [{"score": 0.9, "collection": "c", "uri": "u.md", "header_path": "A > B", "content": "hello"}]}
        else:
            out = {"added": [body["uri"]], "changed": [], "unchanged": []}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def api(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HIPPO_URL", f"http://127.0.0.1:{srv.server_port}")
    CALLS.clear()
    AUTH.clear()
    monkeypatch.delenv("HIPPO_TOKEN", raising=False)
    yield
    srv.shutdown()


def test_search_formats_results(api):
    out = mcp_server.search("q", "c", 3)
    assert "A > B" in out and "hello" in out and "0.900" in out
    assert CALLS[0] == ("/v1/search", {"query": "q", "collections": ["c"], "top_k": 3})


def test_token_is_sent_only_when_configured(api, monkeypatch):
    mcp_server.search("q")
    assert AUTH == [None]
    monkeypatch.setenv("HIPPO_TOKEN", "hip_abc")
    mcp_server.search("q")
    assert AUTH[-1] == "Bearer hip_abc"


def test_index_doc(api, tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# T\nbody")
    assert "added" in mcp_server.index_doc(str(f))
    path, body = CALLS[0]
    assert path == "/v1/ingest/file" and body["collection"] == "docs" and body["uri"].startswith("file://")


def test_index_doc_refuses_infra_and_missing(api, tmp_path):
    d = tmp_path / "private-infra" / "docs"
    d.mkdir(parents=True)
    (d / "x.md").write_text("secret ips")
    assert "excluded" in mcp_server.index_doc(str(d / "x.md"))
    assert "Not a file" in mcp_server.index_doc(str(tmp_path / "nope.md"))
    assert CALLS == []


def test_unreachable_api(monkeypatch):
    monkeypatch.setenv("HIPPO_URL", "http://127.0.0.1:9")
    with pytest.raises(RuntimeError, match="cannot reach"):
        mcp_server.search("q")


def test_readonly_mode_leaves_out_the_indexing_tool(monkeypatch):
    monkeypatch.setenv("HIPPO_MCP_READONLY", "1")
    tools = asyncio.run(mcp_server.build_server().list_tools())
    assert {t.name for t in tools} == {"hippocampus_search"}


def test_tools_registered():
    tools = asyncio.run(mcp_server.build_server().list_tools())
    assert {t.name for t in tools} == {"hippocampus_search", "hippocampus_index_doc"}
