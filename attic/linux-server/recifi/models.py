"""Pydantic models shared by the API, the store and the render pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TransportName = Literal["null", "usb", "bluetooth", "ble"]
ProtocolName = Literal["escpos", "niimbot", "raw"]


class PrinterConfig(BaseModel):
    """Everything needed to find, talk to and lay out for the printer."""

    transport: TransportName = "null"
    # /dev/usb/lp0, "usb:0483:5743", "AA:BB:CC:DD:EE:FF" -- transport decides.
    address: str = ""
    protocol: ProtocolName = "escpos"

    # Head geometry. 384 dots is a 48mm head at 203dpi; 576 is 72mm.
    width_dots: int = Field(default=384, ge=64, le=1728)
    dpi: int = Field(default=203, ge=100, le=600)
    # Burn time / darkness. Meaning is protocol specific, 0-15 is a safe range.
    density: int = Field(default=3, ge=0, le=15)
    # Niimbot media type: 1=gap, 2=black mark, 3=continuous.
    label_type: int = Field(default=1, ge=1, le=3)
    # Blank dots fed after each label so it clears the tear bar.
    feed_dots: int = Field(default=48, ge=0, le=400)

    auto_connect: bool = True
    # Seconds between background health probes.
    health_interval: float = Field(default=10.0, ge=2.0, le=300.0)


class TemplateField(BaseModel):
    """One row of a key/value block."""

    label: str = ""
    value: str = ""
    # Rendered a size up and in the heavier face.
    emphasis: bool = False


class QRSpec(BaseModel):
    value: str = ""
    # Module size in dots; the QR is scaled to fit whichever box it lands in.
    scale: int = Field(default=4, ge=1, le=16)
    position: Literal["top", "bottom", "left", "right"] = "bottom"
    caption: str = ""


class LabelTemplate(BaseModel):
    id: str = ""
    name: str = "Untitled"
    description: str = ""

    title: str = ""
    fields: list[TemplateField] = Field(default_factory=list)
    qr: QRSpec | None = None
    footer: str = ""

    # Append every key of the incoming payload that no field already consumed.
    auto_fields: bool = False

    title_size: int = Field(default=30, ge=8, le=200)
    text_size: int = Field(default=22, ge=8, le=200)
    margin: int = Field(default=10, ge=0, le=100)
    line_gap: int = Field(default=4, ge=0, le=50)
    align: Literal["left", "center"] = "left"
    # 0 = grow to fit content.
    min_height: int = Field(default=0, ge=0, le=4000)


class PrintRequest(BaseModel):
    """Body of POST /api/print and /api/preview.

    `data` accepts an object (K:V rows / placeholder substitution), a string
    (printed as-is, or used as the QR payload when the template has one), or a
    list of {label, value} pairs.
    """

    template: str | None = None
    data: dict[str, Any] | list[Any] | str | None = None
    copies: int = Field(default=1, ge=1, le=20)
    # One-off template supplied inline instead of by id.
    inline: LabelTemplate | None = None


class ConnectRequest(BaseModel):
    transport: TransportName
    address: str = ""
    protocol: ProtocolName | None = None


class RawRequest(BaseModel):
    """Reverse-engineering hatch: push arbitrary bytes at the printer."""

    hex: str = ""
    text: str = ""
    read_reply: bool = False
    read_timeout: float = Field(default=1.0, ge=0.0, le=15.0)
