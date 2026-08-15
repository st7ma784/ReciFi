import asyncio
import json

from recifi import server
from recifi.server import Router, unquote


def test_unquote():
    assert unquote("a%20b") == "a b"
    assert unquote("a+b") == "a b"
    assert unquote("plain") == "plain"
    assert unquote("%zz") == "%zz"  # malformed escapes pass through


def test_router_matches_and_extracts_params():
    router = Router()

    async def handler(request):
        return {"id": request.params["id"]}

    router.add("GET", "/api/templates/{id}", handler)
    fn, params = router.match("GET", "/api/templates/asset-tag")
    assert fn is handler
    assert params == {"id": "asset-tag"}


def test_router_distinguishes_segment_counts():
    router = Router()

    async def one(request):
        return {}

    async def two(request):
        return {}

    router.add("POST", "/api/print", one)
    router.add("POST", "/api/print/{id}", two)
    assert router.match("POST", "/api/print")[0] is one
    assert router.match("POST", "/api/print/qr-only")[0] is two


def test_router_reports_405_when_only_the_method_differs():
    router = Router()

    async def handler(request):
        return {}

    router.add("GET", "/api/status", handler)
    assert router.match("POST", "/api/status")[0] == 405
    assert router.match("GET", "/api/nothing")[0] is None


def test_router_decodes_percent_escaped_params():
    router = Router()

    async def handler(request):
        return {}

    router.add("GET", "/api/templates/{id}", handler)
    assert router.match("GET", "/api/templates/shelf%20tag")[1] == {"id": "shelf tag"}


# ---------------------------------------------------------- over a socket

PORT = 8123


def _request(raw):
    """Start the server, make one real HTTP request, return the raw response."""
    router = Router()

    @router.get("/hello")
    async def hello(request):
        return {"ok": True, "q": request.query.get("x", "")}

    @router.post("/echo")
    async def echo(request):
        return {"got": request.json()}

    @router.get("/stream")
    async def stream(request):
        return server.Response(status=200, content_type="text/plain",
                               stream=(b"abc", b"defg"))

    @router.get("/boom")
    async def boom(request):
        raise ValueError("deliberate")

    async def main():
        srv = await server.serve(router, "127.0.0.1", PORT)
        reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
        writer.write(raw)
        await writer.drain()
        out = b""
        while True:
            chunk = await reader.read(256)
            if not chunk:
                break
            out += chunk
        writer.close()
        srv.close()
        return out

    return asyncio.run(main())


def _split(response):
    head, _, body = response.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, head, body


def test_get_returns_json():
    status, head, body = _split(_request(b"GET /hello?x=7 HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == 200
    assert b"application/json" in head
    assert json.loads(body) == {"ok": True, "q": "7"}


def test_post_body_is_parsed():
    payload = b'{"a":1}'
    raw = b"POST /echo HTTP/1.1\r\nHost: t\r\nContent-Length: %d\r\n\r\n%s" % (len(payload), payload)
    status, _, body = _split(_request(raw))
    assert status == 200
    assert json.loads(body) == {"got": {"a": 1}}


def test_unknown_route_is_404():
    status, _, _ = _split(_request(b"GET /nope HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == 404


def test_wrong_method_is_405():
    status, _, _ = _split(_request(b"DELETE /hello HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == 405


def test_handler_exception_becomes_500_not_a_dropped_socket():
    status, _, body = _split(_request(b"GET /boom HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == 500
    assert b"deliberate" in body


def test_streamed_response_is_chunked_correctly():
    status, head, body = _split(_request(b"GET /stream HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == 200
    assert b"chunked" in head
    assert body == b"3\r\nabc\r\n4\r\ndefg\r\n0\r\n\r\n"


def test_oversized_body_is_rejected():
    raw = b"POST /echo HTTP/1.1\r\nHost: t\r\nContent-Length: 99999\r\n\r\n"
    status, _, _ = _split(_request(raw))
    assert status == 400
