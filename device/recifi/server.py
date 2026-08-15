"""A small asyncio HTTP/1.1 server.

MicroPython has no web framework, so this is the minimum that serves the UI and
the JSON API safely: bounded request parsing (a microcontroller must never
allocate an unbounded header or body), a router with {param} segments, and
file serving that streams off flash instead of reading whole files into RAM.

Responses close the connection rather than keeping it alive -- with one board,
one client and a handful of assets, keep-alive buys little and costs a socket
timeout policy we would otherwise have to get right.
"""

import asyncio
import json

MAX_LINE = 512
MAX_HEADERS = 24
MAX_BODY = 12288

STATUS = {
    200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request",
    404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
    500: "Internal Server Error", 503: "Service Unavailable",
}

TYPES = {
    "html": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "json": "application/json",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
}


def unquote(text):
    if "%" not in text and "+" not in text:
        return text
    text = text.replace("+", " ")
    parts = text.split("%")
    out = [parts[0]]
    for part in parts[1:]:
        try:
            out.append(chr(int(part[:2], 16)) + part[2:])
        except ValueError:
            out.append("%" + part)
    return "".join(out)


class Response:
    def __init__(self, body=b"", status=200, content_type="application/json",
                 headers=None, stream=None):
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self.stream = stream
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body


def json_response(obj, status=200):
    return Response(json.dumps(obj), status, "application/json")


def error(status, message):
    return json_response({"detail": message}, status)


class Request:
    def __init__(self, method, path, query, headers, body):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.params = {}

    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    def text(self):
        return self.body.decode("utf-8", "replace") if self.body else ""


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        self.routes.append((method, pattern.strip("/").split("/"), handler))

    def get(self, pattern):
        def wrap(fn):
            self.add("GET", pattern, fn)
            return fn
        return wrap

    def post(self, pattern):
        def wrap(fn):
            self.add("POST", pattern, fn)
            return fn
        return wrap

    def put(self, pattern):
        def wrap(fn):
            self.add("PUT", pattern, fn)
            return fn
        return wrap

    def delete(self, pattern):
        def wrap(fn):
            self.add("DELETE", pattern, fn)
            return fn
        return wrap

    def match(self, method, path):
        parts = path.strip("/").split("/")
        allowed = False
        for route_method, pattern, handler in self.routes:
            if len(pattern) != len(parts):
                continue
            params = {}
            for expected, actual in zip(pattern, parts):
                if expected.startswith("{") and expected.endswith("}"):
                    params[expected[1:-1]] = unquote(actual)
                elif expected != actual:
                    break
            else:
                if route_method == method:
                    return handler, params
                allowed = True
        return (None, None) if not allowed else (405, None)


async def _read_line(reader):
    line = await reader.readline()
    if len(line) > MAX_LINE:
        raise ValueError("header line too long")
    return line


async def _parse(reader):
    line = await _read_line(reader)
    if not line:
        return None
    try:
        method, target, _ = line.decode().split(" ", 2)
    except ValueError:
        raise ValueError("bad request line")

    query = {}
    if "?" in target:
        target, _, qs = target.partition("?")
        for pair in qs.split("&"):
            if not pair:
                continue
            key, _, value = pair.partition("=")
            query[unquote(key)] = unquote(value)

    headers = {}
    for _ in range(MAX_HEADERS):
        line = await _read_line(reader)
        if not line or line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode().partition(":")
        headers[key.strip().lower()] = value.strip()
    else:
        raise ValueError("too many headers")

    body = b""
    length = int(headers.get("content-length", 0) or 0)
    if length > MAX_BODY:
        raise ValueError("body too large")
    if length:
        body = await reader.readexactly(length)

    return Request(method, unquote(target), query, headers, body)


async def _send(writer, response):
    head = "HTTP/1.1 %d %s\r\n" % (response.status, STATUS.get(response.status, "OK"))
    head += "Content-Type: %s\r\n" % response.content_type
    head += "Connection: close\r\n"
    # The UI polls status; letting a proxy or browser cache that would freeze it.
    head += "Cache-Control: no-store\r\n"
    for key, value in response.headers.items():
        head += "%s: %s\r\n" % (key, value)
    if response.stream is None:
        head += "Content-Length: %d\r\n\r\n" % len(response.body)
        writer.write(head.encode())
        if response.body:
            writer.write(response.body)
        await writer.drain()
    else:
        head += "Transfer-Encoding: chunked\r\n\r\n"
        writer.write(head.encode())
        await writer.drain()
        for chunk in response.stream:
            if not chunk:
                continue
            writer.write(b"%x\r\n" % len(chunk))
            writer.write(chunk)
            writer.write(b"\r\n")
            await writer.drain()
        writer.write(b"0\r\n\r\n")
        await writer.drain()


def file_response(path, content_type=None):
    """Stream a file off flash in small chunks."""
    try:
        size = 0
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
    except OSError:
        return error(404, "no such file")
    if content_type is None:
        content_type = TYPES.get(path.rsplit(".", 1)[-1], "application/octet-stream")

    def chunks():
        with open(path, "rb") as handle:
            while True:
                block = handle.read(512)
                if not block:
                    break
                yield block

    del size
    return Response(status=200, content_type=content_type, stream=chunks())


async def serve(router, host="0.0.0.0", port=80, on_error=None):
    async def handle(reader, writer):
        try:
            try:
                request = await _parse(reader)
            except (ValueError, EOFError) as exc:
                await _send(writer, error(400, str(exc)))
                return
            if request is None:
                return

            handler, params = router.match(request.method, request.path)
            if handler is None:
                response = error(404, "no route for %s" % request.path)
            elif handler == 405:
                response = error(405, "%s not allowed here" % request.method)
            else:
                request.params = params
                try:
                    # Every handler is a coroutine -- uniform beats clever here.
                    response = await handler(request)
                except Exception as exc:
                    if on_error:
                        on_error(exc)
                    response = error(500, "%s: %s" % (type(exc).__name__, exc))
            if isinstance(response, dict):
                response = json_response(response)
            elif response is None:
                response = Response(b"", 204, "text/plain")
            await _send(writer, response)
        except OSError:
            pass  # client hung up mid-response
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, AttributeError):
                pass

    return await asyncio.start_server(handle, host, port)
