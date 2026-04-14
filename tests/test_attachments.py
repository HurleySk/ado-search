import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ado_search.attachments import (
    extract_attachments,
    extract_inline_images,
    rewrite_inline_images,
    download_work_item_attachments,
    download_work_item_inline_images,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture():
    with open(FIXTURE_DIR / "work_item_with_attachments.json") as f:
        return json.load(f)


def test_extract_attachments_from_relations():
    raw = _load_fixture()
    atts = extract_attachments(raw)
    assert len(atts) == 2
    assert atts[0]["name"] == "screenshot.png"
    assert atts[0]["size"] == 45321
    assert atts[0]["guid"] == "111-222-333"
    assert atts[1]["name"] == "design-doc.pdf"
    assert atts[1]["size"] == 102400
    assert atts[1]["guid"] == "444-555-666"


def test_extract_attachments_no_relations():
    raw = {"id": 1, "fields": {}}
    atts = extract_attachments(raw)
    assert atts == []


def test_extract_attachments_filters_non_attachment_relations():
    raw = _load_fixture()
    atts = extract_attachments(raw)
    # Only AttachedFile relations, not hierarchy links
    for att in atts:
        assert "wit/attachments/" in att["url"]
    assert len(atts) == 2  # parent link excluded


def test_extract_inline_images_from_html():
    html = '<p>Text</p><img src="https://dev.azure.com/contoso/_apis/wit/attachments/aaa-bbb-ccc" />'
    images = extract_inline_images(html)
    assert len(images) == 1
    assert images[0]["guid"] == "aaa-bbb-ccc"
    assert "aaa-bbb-ccc" in images[0]["url"]


def test_extract_inline_images_no_images():
    html = "<p>Just text, no images</p>"
    images = extract_inline_images(html)
    assert images == []


def test_extract_inline_images_ignores_external():
    html = '<img src="https://example.com/photo.png" /><img src="https://dev.azure.com/contoso/_apis/wit/attachments/abc-123" />'
    images = extract_inline_images(html)
    assert len(images) == 1
    assert images[0]["guid"] == "abc-123"


def test_extract_inline_images_empty_html():
    assert extract_inline_images("") == []
    assert extract_inline_images(None) == []


def test_rewrite_inline_images():
    html = '<p><img src="https://dev.azure.com/contoso/_apis/wit/attachments/aaa-bbb" /></p>'
    rewritten = rewrite_inline_images(html, {
        "https://dev.azure.com/contoso/_apis/wit/attachments/aaa-bbb": "attachments/1/inline/aaa-bbb.png"
    })
    assert "attachments/1/inline/aaa-bbb.png" in rewritten
    assert "dev.azure.com" not in rewritten


def test_rewrite_inline_images_empty_map():
    html = "<p>no changes</p>"
    assert rewrite_inline_images(html, {}) == html


def test_download_skips_existing(tmp_path):
    """Attachments that already exist with correct size are not re-downloaded."""
    att_dir = tmp_path / "attachments" / "100"
    att_dir.mkdir(parents=True)
    existing = att_dir / "file.txt"
    existing.write_bytes(b"x" * 50)

    attachments = [{"url": "https://ado/att/guid1", "name": "file.txt", "size": 50, "guid": "guid1"}]

    with patch("ado_search.runner.download_binary", new_callable=AsyncMock) as mock_dl:
        result = asyncio.run(download_work_item_attachments(
            100, attachments, data_dir=tmp_path,
            auth_method="pat", org="https://dev.azure.com/co", pat="fake",
        ))

    mock_dl.assert_not_called()
    assert len(result) == 1
    assert result[0]["local_path"] == "attachments/100/file.txt"


def test_download_calls_download_binary(tmp_path):
    """Attachments that don't exist trigger a download."""
    attachments = [{"url": "https://ado/att/guid1", "name": "new.png", "size": 100, "guid": "guid1"}]

    with patch("ado_search.runner.download_binary", new_callable=AsyncMock, return_value=None) as mock_dl:
        result = asyncio.run(download_work_item_attachments(
            200, attachments, data_dir=tmp_path,
            auth_method="pat", org="https://dev.azure.com/co", pat="fake",
        ))

    mock_dl.assert_called_once()
    assert result[0]["local_path"] == "attachments/200/new.png"
    assert "download_error" not in result[0]


def test_download_records_error(tmp_path):
    """Download errors are recorded in metadata."""
    attachments = [{"url": "https://ado/att/guid1", "name": "fail.bin", "size": 100, "guid": "guid1"}]

    with patch("ado_search.runner.download_binary", new_callable=AsyncMock, return_value="HTTP 404"):
        result = asyncio.run(download_work_item_attachments(
            300, attachments, data_dir=tmp_path,
            auth_method="pat", org="https://dev.azure.com/co", pat="fake",
        ))

    assert result[0]["download_error"] == "HTTP 404"


def test_download_inline_images(tmp_path):
    images = [{"url": "https://ado/_apis/wit/attachments/abc-123", "guid": "abc-123"}]

    with patch("ado_search.runner.download_binary", new_callable=AsyncMock, return_value=None):
        image_map, metadata = asyncio.run(download_work_item_inline_images(
            400, images, data_dir=tmp_path,
            auth_method="pat", org="https://dev.azure.com/co", pat="fake",
            source_field="description",
        ))

    assert "https://ado/_apis/wit/attachments/abc-123" in image_map
    assert metadata[0]["source_field"] == "description"
    assert metadata[0]["local_path"] == "attachments/400/inline/abc-123.png"


def test_download_inline_images_skips_existing(tmp_path):
    img_dir = tmp_path / "attachments" / "500" / "inline"
    img_dir.mkdir(parents=True)
    (img_dir / "abc-123.png").write_bytes(b"img data")

    images = [{"url": "https://ado/att/abc-123", "guid": "abc-123"}]

    with patch("ado_search.runner.download_binary", new_callable=AsyncMock) as mock_dl:
        image_map, metadata = asyncio.run(download_work_item_inline_images(
            500, images, data_dir=tmp_path,
            auth_method="pat", org="https://dev.azure.com/co", pat="fake",
        ))

    mock_dl.assert_not_called()
    assert "https://ado/att/abc-123" in image_map
