# src/ado_search/attachments.py
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, unquote


ADO_ATTACHMENT_PATTERN = re.compile(r"_apis/wit/attachments/([0-9a-fA-F\-]+)")


def extract_attachments(raw: dict) -> list[dict]:
    """Extract file attachment metadata from a work item's relations array."""
    relations = raw.get("relations") or []
    attachments = []
    for rel in relations:
        if rel.get("rel") != "AttachedFile":
            continue
        url = rel.get("url", "")
        attrs = rel.get("attributes", {})
        name = attrs.get("name", "")
        size = attrs.get("resourceSize", 0)

        # Extract GUID from URL
        match = ADO_ATTACHMENT_PATTERN.search(url)
        guid = match.group(1) if match else ""

        if url and guid:
            attachments.append({
                "url": url,
                "name": name or f"{guid}.bin",
                "size": size,
                "guid": guid,
            })
    return attachments


class _ImgSrcCollector(HTMLParser):
    """Collects src attributes from img tags matching ADO attachment URLs."""
    def __init__(self):
        super().__init__()
        self.images: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        attrs_dict = dict(attrs)
        src = attrs_dict.get("src", "")
        match = ADO_ATTACHMENT_PATTERN.search(src)
        if match:
            self.images.append({
                "url": src,
                "guid": match.group(1),
            })


def extract_inline_images(html: str) -> list[dict]:
    """Find inline images in HTML that reference ADO attachment URLs."""
    if not html:
        return []
    collector = _ImgSrcCollector()
    collector.feed(html)
    return collector.images


def rewrite_inline_images(html: str, image_map: dict[str, str]) -> str:
    """Replace ADO attachment URLs with local paths in HTML."""
    result = html
    for url, local_path in image_map.items():
        result = result.replace(url, local_path)
    return result


def _safe_filename(name: str, guid: str, seen: set[str]) -> str:
    """Resolve filename conflicts by prepending GUID prefix."""
    if name not in seen:
        seen.add(name)
        return name
    safe = f"{guid[:8]}_{name}"
    seen.add(safe)
    return safe


async def download_work_item_attachments(
    work_item_id: int,
    attachments: list[dict],
    *,
    data_dir: Path,
    auth_method: str,
    org: str,
    pat: str = "",
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Download file attachments. Returns attachment metadata with local_path added.

    Skips files that already exist with the expected size (incremental).
    """
    from ado_search.runner import download_binary

    if not attachments:
        return []

    base = data_dir / "attachments" / str(work_item_id)
    seen_names: set[str] = set()
    results = []

    for att in attachments:
        filename = _safe_filename(att["name"], att["guid"], seen_names)
        dest = base / filename
        rel_path = f"attachments/{work_item_id}/{filename}"

        record = {
            "name": att["name"],
            "size": att["size"],
            "guid": att["guid"],
            "local_path": rel_path,
        }

        # Skip if already downloaded with correct size
        if dest.exists() and att["size"] and dest.stat().st_size == att["size"]:
            results.append(record)
            continue

        err = await download_binary(
            auth_method, url=att["url"], dest_path=dest,
            org=org, pat=pat, semaphore=semaphore,
        )
        if err:
            record["download_error"] = err
        results.append(record)

    return results


async def download_work_item_inline_images(
    work_item_id: int,
    images: list[dict],
    *,
    data_dir: Path,
    auth_method: str,
    org: str,
    pat: str = "",
    semaphore: asyncio.Semaphore | None = None,
    source_field: str = "",
) -> tuple[dict[str, str], list[dict]]:
    """Download inline images. Returns (url→local_path map, metadata list)."""
    from ado_search.runner import download_binary

    if not images:
        return {}, []

    base = data_dir / "attachments" / str(work_item_id) / "inline"
    image_map: dict[str, str] = {}
    metadata: list[dict] = []

    for img in images:
        filename = f"{img['guid']}.png"
        dest = base / filename
        rel_path = f"attachments/{work_item_id}/inline/{filename}"

        # Skip if already downloaded
        if dest.exists():
            image_map[img["url"]] = rel_path
            metadata.append({
                "guid": img["guid"],
                "local_path": rel_path,
                "source_field": source_field,
            })
            continue

        err = await download_binary(
            auth_method, url=img["url"], dest_path=dest,
            org=org, pat=pat, semaphore=semaphore,
        )
        if not err:
            image_map[img["url"]] = rel_path
        metadata.append({
            "guid": img["guid"],
            "local_path": rel_path,
            "source_field": source_field,
            **({"download_error": err} if err else {}),
        })

    return image_map, metadata
