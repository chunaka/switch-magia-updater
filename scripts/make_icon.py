"""Genera el icono de la aplicación sin dependencias externas.

El resultado es un ICO con un PNG de 256x256. Se ejecuta manualmente:

    python scripts/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 256
SCALE = 3
OUTER = SIZE * SCALE
MARGIN = 18 * SCALE
RADIUS = 58 * SCALE
SPARKLE_OUTER = 92 * SCALE
SPARKLE_INNER = 26 * SCALE
START = (125, 58, 237)
END = (236, 72, 153)
WHITE = (255, 255, 255)


def _rounded_rect_contains(x: float, y: float) -> bool:
    left = MARGIN
    top = MARGIN
    right = OUTER - MARGIN
    bottom = OUTER - MARGIN
    clamped_x = min(max(x, left + RADIUS), right - RADIUS)
    clamped_y = min(max(y, top + RADIUS), bottom - RADIUS)
    return (x - clamped_x) ** 2 + (y - clamped_y) ** 2 <= RADIUS**2


def _star(center: float, outer: float, inner: float, points: int = 4) -> list[tuple[float, float]]:
    vertices: list[tuple[float, float]] = []
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / points
        vertices.append(
            (center + radius * math.cos(angle), center + radius * math.sin(angle))
        )
    return vertices


def _polygon_contains(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = len(polygon) - 1
    for current in range(len(polygon)):
        x1, y1 = polygon[current]
        x2, y2 = polygon[previous]
        if (y1 > py) != (y2 > py):
            x_intersection = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < x_intersection:
                inside = not inside
        previous = current
    return inside


def _blend(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(
        int(round(start[channel] + (end[channel] - start[channel]) * ratio))
        for channel in range(3)
    )


def _render() -> list[list[tuple[int, int, int, int]]]:
    sparkle = _star(OUTER / 2, SPARKLE_OUTER, SPARKLE_INNER)
    pixels: list[list[tuple[int, int, int, int]]] = []
    for y in range(OUTER):
        row: list[tuple[int, int, int, int]] = []
        for x in range(OUTER):
            if not _rounded_rect_contains(x + 0.5, y + 0.5):
                row.append((0, 0, 0, 0))
                continue
            ratio = (x + y) / (2 * OUTER)
            red, green, blue = _blend(START, END, ratio)
            if _polygon_contains(x + 0.5, y + 0.5, sparkle):
                row.append((*WHITE, 245))
            else:
                row.append((red, green, blue, 255))
        pixels.append(row)
    return pixels


def _downsample(pixels: list[list[tuple[int, int, int, int]]]) -> list[bytes]:
    rows: list[bytes] = []
    for y in range(0, OUTER, SCALE):
        row = bytearray()
        for x in range(0, OUTER, SCALE):
            red = green = blue = alpha = 0
            for dy in range(SCALE):
                for dx in range(SCALE):
                    r, g, b, a = pixels[y + dy][x + dx]
                    red += r * a
                    green += g * a
                    blue += b * a
                    alpha += a
            if alpha == 0:
                row.extend((0, 0, 0, 0))
            else:
                row.extend(
                    (
                        red // alpha,
                        green // alpha,
                        blue // alpha,
                        alpha // (SCALE * SCALE),
                    )
                )
        rows.append(bytes(row))
    return rows


def _png(rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _ico(png: bytes) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "src/magia_updater/assets/magia.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = _downsample(_render())
    output.write_bytes(_ico(_png(rows)))
    print(f"Icono escrito en {output}")


if __name__ == "__main__":
    main()
