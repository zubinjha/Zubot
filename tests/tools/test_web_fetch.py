import importlib

from src.zubot.tools.kernel.web_fetch import fetch_url

web_fetch_module = importlib.import_module("src.zubot.tools.kernel.web_fetch")


class _FakeHeaders(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html", status: int = 200):
        self._body = body
        self.headers = _FakeHeaders({"Content-Type": content_type})
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_url_rejects_non_http():
    result = fetch_url("file:///tmp/a.txt")
    assert not result["ok"]
    assert "http/https" in result["error"]


def test_fetch_url_extracts_html(monkeypatch):
    html = b"""
    <html><head><title>Example Title</title></head>
    <body><h1>Hello</h1><p>World</p><script>ignore_me()</script></body></html>
    """

    def fake_urlopen(req, timeout=10):
        return _FakeResponse(html, "text/html; charset=utf-8", 200)

    monkeypatch.setattr(web_fetch_module, "urlopen", fake_urlopen)
    result = fetch_url("https://example.com")
    assert result["ok"]
    assert result["title"] == "Example Title"
    assert "Hello World" in result["text"]
    assert "ignore_me" not in result["text"]


def test_fetch_url_text_plain(monkeypatch):
    text = b"line one\nline two\n"

    def fake_urlopen(req, timeout=10):
        return _FakeResponse(text, "text/plain", 200)

    monkeypatch.setattr(web_fetch_module, "urlopen", fake_urlopen)
    result = fetch_url("https://example.com/txt")
    assert result["ok"]
    assert result["content_type"] == "text/plain"
    assert "line one" in result["text"]


def test_fetch_url_network_error(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise RuntimeError("boom")

    monkeypatch.setattr(web_fetch_module, "urlopen", fake_urlopen)
    result = fetch_url("https://example.com")
    assert not result["ok"]
    assert result["source"] == "web_fetch_error"
    assert "boom" in result["error"]
