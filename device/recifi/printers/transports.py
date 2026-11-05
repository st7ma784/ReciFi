"""Ways to get bytes to the printer.

Three of them, for three situations:

  ble   -- the Pico 2 W's radio. NOTE: MicroPython exposes BLE only, not
           Bluetooth Classic/SPP. A printer that only speaks Classic cannot be
           reached from this board at all, whatever we send.
  uart  -- wired TTL serial. Works regardless of what the radio can do, and is
           the fallback if the printer turns out to be Classic-only.
  null  -- spools to flash so the whole stack can be exercised with no printer.
"""

import asyncio
import time

try:
    from machine import UART, Pin

    _HAVE_UART = True
except ImportError:
    _HAVE_UART = False

try:
    import aioble
    import bluetooth

    _HAVE_BLE = True
except ImportError:
    _HAVE_BLE = False

# Names seen on rebadged pocket thermal printers.
_HINTS = ("print", "orgbro", "z3", "label", "niimbot", "phomemo", "btp", "pt-")

_ADDR_PUBLIC = 0
_ADDR_RANDOM = 1

_WRITE = 0x08
_WRITE_NO_RESPONSE = 0x04
_EALREADY = 114

if _HAVE_BLE:
    # 18F0/2AF1 is the de-facto printing service on cheap BLE thermal printers
    # (the ORGBRO Z3 included) -- prefer it when the printer offers it.
    _PRINT_SERVICE = bluetooth.UUID(0x18F0)
    _PRINT_CHAR = bluetooth.UUID(0x2AF1)
    # Generic Access / Attribute / Device Information. Writing to Generic
    # Access does not print anything -- it renames the device.
    _SKIP_SERVICES = (bluetooth.UUID(0x1800), bluetooth.UUID(0x1801), bluetooth.UUID(0x180A))
else:
    _PRINT_SERVICE = _PRINT_CHAR = None
    _SKIP_SERVICES = ()


class TransportError(Exception):
    """Anything that stops bytes reaching the printer."""


class Transport:
    name = "base"
    address_hint = ""
    available = True
    unavailable_reason = ""

    def __init__(self, address=""):
        self.address = address
        self.connected = False

    @staticmethod
    async def discover():
        return []

    async def connect(self):
        raise NotImplementedError

    async def disconnect(self):
        self.connected = False

    async def write(self, data):
        raise NotImplementedError

    async def read(self, size=64, timeout=1.0):
        return b""

    async def probe(self):
        return (self.connected, "link open" if self.connected else "not connected")


# ------------------------------------------------------------------- null

SPOOL = "spool.bin"
# Flash has limited write endurance, so the spool is capped and rewritten
# rather than appended to forever.
SPOOL_LIMIT = 32768


class NullTransport(Transport):
    name = "null"
    address_hint = "ignored -- output is spooled to flash"

    def __init__(self, address=""):
        super().__init__(address)
        self.written = 0
        self._handle = None

    @staticmethod
    async def discover():
        return [{"transport": "null", "address": "spool", "name": "Virtual printer",
                 "detail": "writes to " + SPOOL, "likely": True}]

    async def connect(self):
        self._handle = open(SPOOL, "wb")
        self.written = 0
        self.connected = True

    async def disconnect(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self.connected = False

    async def write(self, data):
        if self._handle is None:
            raise TransportError("not connected")
        if self.written < SPOOL_LIMIT:
            self._handle.write(data)
            self._handle.flush()
        self.written += len(data)

    async def probe(self):
        return (self.connected, "spooled %d bytes" % self.written)


# ------------------------------------------------------------------- uart


class UARTTransport(Transport):
    name = "uart"
    address_hint = "configured by uart_id / uart_tx / uart_rx / uart_baud"
    available = _HAVE_UART
    unavailable_reason = "" if _HAVE_UART else "no machine.UART on this port"

    def __init__(self, address="", cfg=None):
        super().__init__(address)
        self.cfg = cfg or {}
        self._uart = None

    @staticmethod
    async def discover():
        if not _HAVE_UART:
            return []
        return [{"transport": "uart", "address": "uart0", "name": "UART 0",
                 "detail": "wire printer RX to the configured TX pin", "likely": True}]

    async def connect(self):
        if not _HAVE_UART:
            raise TransportError(self.unavailable_reason)
        cfg = self.cfg
        try:
            self._uart = UART(
                int(cfg.get("uart_id", 0)),
                baudrate=int(cfg.get("uart_baud", 9600)),
                tx=Pin(int(cfg.get("uart_tx", 0))),
                rx=Pin(int(cfg.get("uart_rx", 1))),
                timeout=1000,
            )
        except (ValueError, OSError) as exc:
            raise TransportError("UART open failed: %s" % exc)
        self.connected = True

    async def disconnect(self):
        if self._uart is not None:
            try:
                self._uart.deinit()
            except (AttributeError, OSError):
                pass
            self._uart = None
        self.connected = False

    async def write(self, data):
        if self._uart is None:
            raise TransportError("not connected")
        view = memoryview(data)
        while view:
            # Serial at 9600 baud is ~1KB/s; yielding keeps the HTTP server alive.
            sent = self._uart.write(view[:256])
            if sent is None:
                raise TransportError("UART write failed")
            view = view[sent:]
            await asyncio.sleep_ms(0)

    async def read(self, size=64, timeout=1.0):
        if self._uart is None:
            return b""
        deadline = time.ticks_add(time.ticks_ms(), int(timeout * 1000))
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self._uart.any():
                return self._uart.read(size) or b""
            await asyncio.sleep_ms(20)
        return b""


# -------------------------------------------------------------------- ble


def _addr_bytes(mac):
    parts = mac.replace("-", ":").split(":")
    if len(parts) != 6:
        raise TransportError("bad BLE address %r" % mac)
    try:
        return bytes(int(p, 16) for p in parts)
    except ValueError:
        raise TransportError("bad BLE address %r" % mac)


class BLETransport(Transport):
    name = "ble"
    address_hint = "AA:BB:CC:DD:EE:FF  (append @<char-uuid> to pin the write handle)"
    available = _HAVE_BLE
    unavailable_reason = "" if _HAVE_BLE else "aioble not installed -- mip install aioble"

    def __init__(self, address=""):
        super().__init__(address)
        self._conn = None
        self._char = None
        self._response = False
        self._mtu = 23

    @staticmethod
    async def discover():
        if not _HAVE_BLE:
            return []
        found = {}
        try:
            async with aioble.scan(5000, interval_us=30000, window_us=30000, active=True) as scanner:
                async for result in scanner:
                    addr = result.device.addr_hex()
                    name = result.name() or ""
                    if addr not in found or (name and not found[addr]["name"]):
                        found[addr] = {
                            "transport": "ble",
                            "address": addr,
                            "name": name or "(unnamed)",
                            "detail": "RSSI %d" % result.rssi,
                            "likely": any(h in name.lower() for h in _HINTS),
                            "rssi": result.rssi,
                        }
        except Exception as exc:
            return [{"transport": "ble", "address": "", "name": "BLE scan failed",
                     "detail": str(exc), "likely": False}]
        out = list(found.values())
        out.sort(key=lambda d: -d.get("rssi", -999))
        return out

    def _split(self):
        if "@" in self.address:
            mac, char = self.address.split("@", 1)
            return mac.strip(), char.strip().lower()
        return self.address.strip(), ""

    async def _find_char(self, wanted):
        """Pick the write characteristic: an explicit one, else the first
        writable characteristic on a vendor service.

        The service list is drained fully before any characteristic discovery
        starts -- aioble permits only one discovery at a time, and nesting the
        two fails with "Discovery in progress".
        """
        services = []
        async for service in self._conn.services():
            services.append(service)

        best = None
        best_score = -1
        for service in services:
            if service.uuid in _SKIP_SERVICES:
                continue
            # A 128-bit UUID is vendor-defined; a 16-bit one is SIG-assigned.
            vendor = len(bytes(service.uuid)) == 16
            chars = []
            async for char in service.characteristics():
                chars.append(char)
            for char in chars:
                if wanted:
                    if str(char.uuid).lower().find(wanted) >= 0:
                        return char
                    continue
                props = getattr(char, "properties", _WRITE)
                if not props & (_WRITE | _WRITE_NO_RESPONSE):
                    continue
                if service.uuid == _PRINT_SERVICE:
                    score = 100 if char.uuid == _PRINT_CHAR else 80
                elif vendor:
                    score = 60
                else:
                    score = 40
                if score > best_score:
                    best, best_score = char, score

        if wanted:
            raise TransportError("characteristic %s not found" % wanted)
        if best is None:
            raise TransportError("no writable characteristic -- pin one with @uuid")
        return best

    async def connect(self):
        if not _HAVE_BLE:
            raise TransportError(self.unavailable_reason)
        mac, wanted = self._split()
        addr = _addr_bytes(mac)

        last = None
        for kind in (_ADDR_PUBLIC, _ADDR_RANDOM):
            device = aioble.Device(kind, addr)
            try:
                self._conn = await device.connect(timeout_ms=10000)
                break
            except Exception as exc:
                last = exc
                self._conn = None
        if self._conn is None:
            raise TransportError(
                "BLE connect to %s failed: %s. Is the printer on, in range and "
                "not already paired to a phone?" % (mac, last)
            )

        # Worth asking: the Z3 grants 247, which makes writes 10x larger than
        # the 23-byte default. exchange_mtu returns the negotiated value on a
        # first call but None on a repeat, so read it back off the connection
        # rather than trusting the return value.
        negotiated = None
        try:
            negotiated = await self._conn.exchange_mtu(247)
        except Exception:
            pass
        if not isinstance(negotiated, int):
            negotiated = getattr(self._conn, "mtu", None)
        self._mtu = negotiated if isinstance(negotiated, int) and negotiated >= 23 else 23

        try:
            self._char = await self._find_char(wanted)
        except Exception as exc:
            await self.disconnect()
            if isinstance(exc, TransportError):
                raise
            # aioble raises bare TypeErrors when the link drops mid-discovery
            # (its conn_handle goes None). Surface that as a normal connect
            # failure so the health loop retries instead of crashing main.
            raise TransportError("BLE link dropped during service discovery: %r" % exc)
        props = getattr(self._char, "properties", _WRITE)
        # Prefer acknowledged writes wherever the characteristic allows them:
        # the stack flow-controls those, whereas write-without-response leaves
        # us guessing at pacing and returns EALREADY when the guess is wrong.
        self._response = bool(props & _WRITE)
        self.address = "%s@%s" % (mac, self._char.uuid)
        self.connected = True

    async def disconnect(self):
        conn, self._conn = self._conn, None
        self._char = None
        self.connected = False
        if conn is not None:
            try:
                await conn.disconnect()
            except Exception:
                pass

    async def write(self, data):
        if self._char is None:
            raise TransportError("not connected")
        view = memoryview(data)
        # Stay inside the negotiated ATT MTU; oversized writes are dropped
        # silently, which shows up as a label with pieces missing.
        chunk = max(20, self._mtu - 3)
        for offset in range(0, len(view), chunk):
            piece = bytes(view[offset : offset + chunk])
            for attempt in range(6):
                try:
                    await self._char.write(piece, response=self._response)
                    break
                except OSError as exc:
                    code = exc.errno if hasattr(exc, "errno") else (exc.args[0] if exc.args else 0)
                    # EALREADY: the previous write has not drained yet. Back off
                    # and retry rather than losing part of the label.
                    if code == _EALREADY and attempt < 5:
                        await asyncio.sleep_ms(20 * (attempt + 1))
                        continue
                    raise TransportError("BLE write failed: %s" % exc)
                except Exception as exc:
                    raise TransportError("BLE write failed: %s" % exc)
            if not self._response:
                # Unacknowledged writes need pacing or the printer's buffer
                # overruns and the label comes out truncated.
                await asyncio.sleep_ms(8)

    async def probe(self):
        if self._conn is None:
            return (False, "not connected")
        if not self._conn.is_connected():
            self.connected = False
            return (False, "BLE link dropped")
        return (True, "BLE %s" % self.address)


TRANSPORTS = {"null": NullTransport, "uart": UARTTransport, "ble": BLETransport}


def make(name, cfg):
    try:
        cls = TRANSPORTS[name]
    except KeyError:
        raise ValueError("unknown transport %r" % name)
    if cls is UARTTransport:
        return cls(cfg.get("address", ""), cfg)
    return cls(cfg.get("address", ""))
