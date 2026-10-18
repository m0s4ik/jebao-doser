"""Gizwits data point schemas: loading, downloading and decoding.

Every Gizwits product has a schema (``product_key`` -> data points with their
byte/bit positions). The device tells its product key over the LAN, and the
schema is public: no account is needed to download it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

BUNDLED = Path(__file__).with_name("schemas")
SCHEMA_URLS = (
    "https://api.gizwits.com/app/datapoint?product_key={pk}",
    "https://euapi.gizwits.com/app/datapoint?product_key={pk}",
    "https://usapi.gizwits.com/app/datapoint?product_key={pk}",
)


class SchemaError(Exception):
    """The schema could not be found or does not fit the data."""


class DataPoints:
    """Decoder built from a product's data point schema."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema
        self.product_key: str | None = schema.get("product_key")
        self.product_name: str | None = schema.get("name")
        self.attrs: list[dict[str, Any]] = schema["entities"][0]["attrs"]
        self.writable = [a for a in self.attrs if a["type"] == "status_writable"]
        self.length = max(a["position"]["byte_offset"] + self._byte_len(a) for a in self.attrs)
        # Bit fields sharing a byte_offset form one big-endian integer,
        # with bit_offset counted from the least significant bit.
        self._groups: dict[int, int] = {}
        for a in self.attrs:
            pos = a["position"]
            if pos["unit"] == "bit":
                self._groups[pos["byte_offset"]] = max(
                    self._groups.get(pos["byte_offset"], 0), self._byte_len(a)
                )

    @staticmethod
    def _byte_len(a: dict[str, Any]) -> int:
        pos = a["position"]
        if pos["unit"] == "bit":
            return (pos["bit_offset"] + pos["len"] + 7) // 8
        return pos["len"]

    @property
    def names(self) -> set[str]:
        return {a["name"] for a in self.attrs}

    @property
    def heads(self) -> int | None:
        """Number of heads from the product name ("4头..." = 4 heads), if stated."""
        m = re.search(r"(\d+)\s*头", self.product_name or "")
        return int(m.group(1)) if m else None

    def decode(self, data: bytes) -> dict[str, Any]:
        if len(data) < self.length:
            raise SchemaError(f"status too short: {len(data)} < {self.length} bytes")
        out: dict[str, Any] = {}
        for a in self.attrs:
            pos = a["position"]
            off = pos["byte_offset"]
            if pos["unit"] == "bit":
                word = int.from_bytes(data[off : off + self._groups[off]], "big")
                value: Any = (word >> pos["bit_offset"]) & ((1 << pos["len"]) - 1)
                if a["data_type"] == "bool":
                    value = bool(value)
                elif a["data_type"] == "enum":
                    names = a.get("enum") or []
                    value = names[value] if value < len(names) else value
            else:
                raw = data[off : off + pos["len"]]
                if a["data_type"].startswith("uint"):
                    spec = a.get("uint_spec") or {}
                    value = int.from_bytes(raw, "big") * spec.get("ratio", 1) + spec.get("addition", 0)
                else:
                    value = bytes(raw)
            out[a["name"]] = value
        return out


def load_file(path: Path | str) -> DataPoints:
    return DataPoints(json.loads(Path(path).read_text(encoding="utf-8")))


def _download(product_key: str) -> dict[str, Any]:
    errors = []
    for url in SCHEMA_URLS:
        try:
            with urllib.request.urlopen(url.format(pk=product_key), timeout=15) as resp:
                schema = json.loads(resp.read().decode("utf-8"))
            if schema.get("entities"):
                return schema
            errors.append(f"{url.split('/')[2]}: no entities")
        except (OSError, ValueError) as err:
            errors.append(f"{url.split('/')[2]}: {err}")
    raise SchemaError(f"schema for {product_key} not found ({'; '.join(errors)})")


def get_schema(product_key: str, cache_dir: Path | str | None = None) -> DataPoints:
    """Bundled schema, then cached copy, then download (and cache it). Blocking."""
    for folder in (BUNDLED, Path(cache_dir) if cache_dir else None):
        if folder and (folder / f"{product_key}.json").exists():
            return load_file(folder / f"{product_key}.json")
    _LOGGER.info("Downloading Gizwits schema for product %s", product_key)
    schema = _download(product_key)
    if cache_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        (Path(cache_dir) / f"{product_key}.json").write_text(
            json.dumps(schema, ensure_ascii=False), encoding="utf-8"
        )
    return DataPoints(schema)


async def async_get_schema(product_key: str, cache_dir: Path | str | None = None) -> DataPoints:
    return await asyncio.to_thread(get_schema, product_key, cache_dir)
