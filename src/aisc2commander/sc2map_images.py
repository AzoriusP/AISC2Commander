from __future__ import annotations

import bz2
from io import BytesIO
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

import mpyq
from PIL import Image, UnidentifiedImageError


MAX_EMBEDDED_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PREFERRED_ASPECT_DELTA = 0.03
_IMAGE_SUFFIXES = {b".jpg", b".jpeg", b".png", b".tga", b".webp"}
_KIND_PRIORITY = {
    "map_image": 0,
    "minimap": 1,
    "preview": 2,
    "screenshot": 3,
}


@dataclass(frozen=True, slots=True)
class EmbeddedMapImage:
    """An image stored inside a local SC2Map archive."""

    name: str
    data: bytes
    kind: str


def extract_embedded_map_image(
    map_path: Path,
    *,
    preferred_aspect_ratio: float | None = None,
) -> EmbeddedMapImage | None:
    """Return a suitable image embedded in a local SC2Map archive.

    With no preferred ratio this preserves the original behavior and returns
    the first published screenshot.  When a world-space aspect ratio is given,
    every viable image is inspected and the closest orthographic candidate is
    selected.  Images that differ by more than three percent are rejected so a
    presentation screenshot is never stretched over the coordinate plane.

    SC2Map files are MPQ archives.  Some Blizzard maps mix compressed and
    uncompressed MPQ sectors in one file; mpyq 0.2.5 treats every full-size
    sector as compressed, so image reads go through the corrected sector reader
    below.
    """

    source = map_path.expanduser().resolve()
    if source.suffix.casefold() != ".sc2map" or not source.is_file():
        return None

    archive = mpyq.MPQArchive(str(source), listfile=False)
    try:
        listfile = _read_mpq_file(archive, b"(listfile)", size_limit=4 * 1024 * 1024)
        archive_files = tuple(line.strip() for line in listfile.splitlines() if line.strip())
        available = {name.lower(): name for name in archive_files}

        screenshot_names = _document_screenshot_names(archive, available)
        candidates: list[tuple[bytes, str]] = [(name, "screenshot") for name in screenshot_names]

        preferred_names = (
            b"MapPreview.jpg",
            b"MapPreview.jpeg",
            b"MapPreview.png",
            b"MapPreview.tga",
            b"Preview.jpg",
            b"Preview.png",
            b"Preview.tga",
        )
        candidates.extend(
            (available[name.lower()], "preview")
            for name in preferred_names
            if name.lower() in available
        )

        # Custom map images are a better fallback than loading screens or the
        # tiny generated minimap. Preserve archive order for deterministic use.
        candidates.extend(
            (name, "map_image")
            for name in archive_files
            if _suffix(name) in _IMAGE_SUFFIXES
            and b"loading" not in name.lower()
            and name.lower() != b"minimap.tga"
        )
        minimap = available.get(b"minimap.tga")
        if minimap is not None:
            candidates.append((minimap, "minimap"))

        ratio = _valid_aspect_ratio(preferred_aspect_ratio)
        ranked: list[tuple[float, int, int, int, EmbeddedMapImage]] = []
        seen: set[bytes] = set()
        for index, (name, kind) in enumerate(candidates):
            normalized = name.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                data = _read_mpq_file(archive, name, size_limit=MAX_EMBEDDED_IMAGE_BYTES)
            except (KeyError, NotImplementedError, RuntimeError, ValueError, zlib.error, OSError):
                continue
            if not data or not _looks_like_supported_image(data, _suffix(name)):
                continue
            embedded = EmbeddedMapImage(_display_name(name), data, kind)
            if ratio is None:
                return embedded
            dimensions = _image_dimensions(data)
            if dimensions is None:
                continue
            width, height = dimensions
            aspect_delta = abs((width / height) / ratio - 1.0)
            ranked.append(
                (
                    aspect_delta,
                    _KIND_PRIORITY.get(kind, 99),
                    -(width * height),
                    index,
                    embedded,
                )
            )
        if not ranked:
            return None
        best = min(ranked)
        return best[-1] if best[0] <= MAX_PREFERRED_ASPECT_DELTA else None
    finally:
        archive.file.close()


def _valid_aspect_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    ratio = float(value)
    return ratio if math.isfinite(ratio) and ratio > 0 else None


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _document_screenshot_names(
    archive: mpyq.MPQArchive,
    available: dict[bytes, bytes],
) -> tuple[bytes, ...]:
    document_name = available.get(b"documentinfo", b"DocumentInfo")
    try:
        raw = _read_mpq_file(archive, document_name, size_limit=4 * 1024 * 1024)
        root = ET.fromstring(raw.decode("utf-8-sig"))
    except (ET.ParseError, KeyError, UnicodeDecodeError, RuntimeError, ValueError, zlib.error):
        return ()

    result: list[bytes] = []
    for value in root.findall("./Screenshot/File/Value"):
        text = (value.text or "").strip()
        if not text:
            continue
        encoded = text.encode("utf-8")
        actual = available.get(encoded.lower())
        if actual is not None and _suffix(actual) in _IMAGE_SUFFIXES:
            result.append(actual)
    return tuple(result)


def _read_mpq_file(
    archive: mpyq.MPQArchive,
    filename: bytes,
    *,
    size_limit: int,
) -> bytes:
    hash_entry = archive.get_hash_table_entry(filename)
    if hash_entry is None:
        raise KeyError(_display_name(filename))
    block = archive.block_table[hash_entry.block_table_index]
    if not block.flags & mpyq.MPQ_FILE_EXISTS or block.archived_size == 0:
        raise KeyError(_display_name(filename))
    if block.size > size_limit:
        raise ValueError(f"SC2Map 内嵌文件超过 {size_limit} 字节限制")
    if block.flags & mpyq.MPQ_FILE_ENCRYPTED:
        raise NotImplementedError("不支持加密的 SC2Map 内嵌文件")
    if block.flags & mpyq.MPQ_FILE_IMPLODE:
        raise NotImplementedError("不支持 PKWARE Implode 压缩")

    archive.file.seek(block.offset + archive.header["offset"])
    archived = archive.file.read(block.archived_size)
    if len(archived) != block.archived_size:
        raise ValueError("SC2Map 内嵌文件已截断")

    if block.flags & mpyq.MPQ_FILE_SINGLE_UNIT:
        if block.flags & mpyq.MPQ_FILE_COMPRESS and block.archived_size < block.size:
            result = _decompress_mpq_sector(archived)
        else:
            result = archived
        if len(result) != block.size:
            raise ValueError("SC2Map 单扇区文件大小不匹配")
        return result

    sector_size = 512 << archive.header["sector_size_shift"]
    sector_count = max(1, (block.size + sector_size - 1) // sector_size)
    offset_count = sector_count + 1
    if block.flags & mpyq.MPQ_FILE_SECTOR_CRC:
        offset_count += 1
    table_size = 4 * offset_count
    if len(archived) < table_size:
        raise ValueError("SC2Map 扇区表已截断")
    offsets = struct.unpack(f"<{offset_count}I", archived[:table_size])
    if offsets[0] < table_size or any(
        left > right or right > len(archived)
        for left, right in zip(offsets, offsets[1:])
    ):
        raise ValueError("SC2Map 扇区偏移无效")

    result = bytearray()
    bytes_left = block.size
    for index in range(sector_count):
        sector = archived[offsets[index] : offsets[index + 1]]
        expected_size = min(sector_size, bytes_left)
        if block.flags & mpyq.MPQ_FILE_COMPRESS and len(sector) < expected_size:
            sector = _decompress_mpq_sector(sector)
        if len(sector) != expected_size:
            raise ValueError("SC2Map 扇区解压后大小不匹配")
        result.extend(sector)
        bytes_left -= expected_size
    if bytes_left != 0 or len(result) != block.size:
        raise ValueError("SC2Map 内嵌文件大小不匹配")
    return bytes(result)


def _decompress_mpq_sector(data: bytes) -> bytes:
    if not data:
        raise ValueError("SC2Map 压缩扇区为空")
    compression_type = data[0]
    payload = data[1:]
    if compression_type == 0:
        return data
    if compression_type == 2:
        return zlib.decompress(payload)
    if compression_type == 16:
        return bz2.decompress(payload)
    raise NotImplementedError(f"不支持的 MPQ 压缩类型：0x{compression_type:02x}")


def _suffix(filename: bytes) -> bytes:
    slash = max(filename.rfind(b"/"), filename.rfind(b"\\"))
    dot = filename.rfind(b".")
    return filename[dot:].lower() if dot > slash else b""


def _display_name(filename: bytes) -> str:
    return filename.decode("utf-8", errors="replace")


def _looks_like_supported_image(data: bytes, suffix: bytes) -> bool:
    if suffix in {b".jpg", b".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if suffix == b".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == b".webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if suffix == b".tga":
        return len(data) >= 18
    return False
