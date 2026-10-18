import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner

from ado_search.cli import main
from ado_search.config import default_config, save_config
from ado_search.mentions import (
    RECENT_MENTIONS_WIQL, comment_plain_text, identity_from_connection_data,
    mention_records, mentions_identity, parse_utc,
)

ORG = "https://dev.azure.com/contoso"
ME_ID = "e338d23d-827a-64c6-a711-2e1e450a01e0"
JANE_ID = "cb99547e-260b-6de7-b9e7-b3b2c45e0cf9"

CONNECTION_DATA = {
    "authenticatedUser": {
        "id": ME_ID,
        "providerDisplayName": "Samuel Hurley",
        "customDisplayName": "Samuel Hurley (CTR)",
        "properties": {"Account": {"$type": "System.String", "$value": "Samuel.Hurley@ferc.gov"}},
    },
}
ME = identity_from_connection_data(CONNECTION_DATA)


def _anchor(person_id, name):
    return f'<a href="#" data-vss-mention="version:2.0,{person_id}">@{name}</a>'


def _person(person_id, name, email):
    return {"id": person_id, "displayName": name, "uniqueName": email}


JANE = _person(JANE_ID, "Jane Doe (CTR)", "Jane.Doe@co.com")
SELF = _person(ME_ID, "Samuel Hurley (CTR)", "Samuel.Hurley@ferc.gov")


def _comment(cid, text, created, *, author=JANE, modified=None):
    return {
        "id": cid, "workItemId": 0, "text": text, "createdBy": author,
        "createdDate": created, "modifiedDate": modified or created,
    }


ITEMS = {
    101: {"id": 101, "fields": {
        "System.TeamProject": "Proj A", "System.Title": "Load fails", "System.State": "Active",
        "System.WorkItemType": "Bug",
        "System.AssignedTo": _person("x", "Pat Lee", "Pat.Lee@co.com"),
    }},
    202: {"id": 202, "fields": {
        "System.TeamProject": "Proj B", "System.Title": "Review", "System.State": "New",
        "System.WorkItemType": "Task",
    }},
}

COMMENTS = {
    101: [
        _comment(11, f"<p>{_anchor(ME_ID, 'Samuel Hurley (CTR)')} please check &amp; reply</p>",
                 "2026-10-16T14:55:28.477Z"),
        _comment(12, f"<div>{_anchor(JANE_ID, 'Jane Doe (CTR)')} FYI, cc @Samuel Hurley (CTR)</div>",
                 "2026-10-17T09:00:00.1Z"),
        _comment(13, f"<div>{_anchor(JANE_ID, 'Jane Doe (CTR)')} self note</div>",
                 "2026-10-17T10:00:00Z", author=SELF),
        _comment(14, f"<p>Answering {_anchor(ME_ID, 'Samuel Hurley (CTR)')}</p>",
                 "2026-10-17T11:00:00Z", author=SELF),
    ],
    202: [
        _comment(21, "<p>@Samuel Hurley (CTR) old plain-text mention</p>",
                 "2026-10-14T08:00:00Z", modified="2026-10-15T12:30:00.1234567Z"),
        _comment(22, "<p>No mention here</p>", "2026-10-17T12:00:00Z"),
        _comment(23, "<p>@Samuel Hurleyson is someone else</p>", "2026-10-17T12:30:00Z"),
        _comment(24, "<p>Old one @samuel hurley</p>", "2026-10-01T00:00:00Z"),
    ],
}


# ── identity and matching ──

def test_identity_from_connection_data():
    assert ME.id == ME_ID
    assert ME.display_name == "Samuel Hurley (CTR)"
    assert ME.email == "Samuel.Hurley@ferc.gov"
    assert set(ME.names) == {"Samuel Hurley (CTR)", "Samuel Hurley", "Samuel.Hurley@ferc.gov"}


def test_identity_without_custom_name_uses_provider_name():
    me = identity_from_connection_data({"authenticatedUser": {"id": "abc", "providerDisplayName": "Jo Ng"}})
    assert me.display_name == "Jo Ng"
    assert me.names == ["Jo Ng"]


def test_anchor_mention_matches_by_id():
    assert mentions_identity(f"<p>{_anchor(ME_ID.upper(), 'Sam')} hi</p>", ME)


def test_anchor_for_someone_else_does_not_match_even_with_name_in_text():
    assert not mentions_identity(COMMENTS[101][1]["text"], ME)


def test_plain_text_name_fallback_without_anchors():
    assert mentions_identity("<p>@Samuel Hurley (CTR) see this</p>", ME)
    assert mentions_identity("<p>ping @samuel&nbsp;hurley, thanks</p>", ME)
    assert mentions_identity("<p>ask @Samuel.Hurley@ferc.gov</p>", ME)
    assert not mentions_identity("<p>@Samuel Hurleyson</p>", ME)
    assert not mentions_identity("<p>mail me at x@Samuel Hurley</p>", ME)
    assert not mentions_identity("<p>Samuel Hurley without an at sign</p>", ME)


def test_comment_plain_text_renders_mentions_and_strips_html():
    text = comment_plain_text(
        f'<p>{_anchor(ME_ID, "Samuel Hurley (CTR)")}&nbsp;and '
        f'<a href="#" data-vss-mention="version:2.0,{JANE_ID}">Jane Doe</a>:</p>'
        "<ul><li>a &gt; b</li></ul>"
    )
    assert text == "@Samuel Hurley (CTR) and @Jane Doe:\na > b"


def test_parse_utc_variants():
    utc = timezone.utc
    assert parse_utc("2026-10-18T02:21:12.27Z") == datetime(2026, 10, 18, 2, 21, 12, 270000, tzinfo=utc)
    assert parse_utc("2026-10-15T12:30:00.1234567Z") == datetime(2026, 10, 15, 12, 30, 0, 123456, tzinfo=utc)
    assert parse_utc("2026-10-15") == datetime(2026, 10, 15, tzinfo=utc)
    assert parse_utc("2026-10-15T00:00:00-04:00") == datetime(2026, 10, 15, 4, tzinfo=utc)
    with pytest.raises(ValueError):
        parse_utc("yesterday")


# ── record building ──

def test_mention_records_default_drops_own_and_non_mentions():
    records = mention_records(ITEMS[101], COMMENTS[101], ME)
    assert [r["comment_id"] for r in records] == [11]
    assert records[0] == {
        "work_item_id": 101, "title": "Load fails", "state": "Active", "type": "Bug",
        "assigned_to": "Pat.Lee@co.com", "comment_id": 11, "author": "Jane Doe (CTR)",
        "created_date": "2026-10-16T14:55:28Z",
        "text": "@Samuel Hurley (CTR) please check & reply",
    }


def test_mention_records_include_own():
    records = mention_records(ITEMS[101], COMMENTS[101], ME, include_own=True)
    assert [r["comment_id"] for r in records] == [11, 14]


def test_mention_records_own_matched_by_email_when_id_differs():
    other_id_self = _person("not-the-vsid", "Samuel Hurley (CTR)", "samuel.hurley@ferc.gov")
    comment = _comment(1, _anchor(ME_ID, "Samuel Hurley (CTR)"), "2026-10-17T00:00:00Z", author=other_id_self)
    assert mention_records(ITEMS[101], [comment], ME) == []


def test_mention_records_since_uses_created_or_modified():
    since = parse_utc("2026-10-15T00:00:00Z")
    records = mention_records(ITEMS[202], COMMENTS[202], ME, since=since)
    assert [r["comment_id"] for r in records] == [21]
    assert records[0]["created_date"] == "2026-10-14T08:00:00Z"
    assert records[0]["assigned_to"] == ""
    assert mention_records(ITEMS[202], COMMENTS[202], ME, since=parse_utc("2026-10-16")) == []


# ── live flow through mocked HTTP ──

def _response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


class FakeAdo:
    """Answers urlopen requests by URL and records them."""

    def __init__(self, ids=(101, 202), fail=()):
        self.ids = list(ids)
        self.fail = set(fail)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        if "/_apis/connectionData" in url:
            return _response(CONNECTION_DATA)
        if "/_apis/wit/wiql" in url:
            return _response({"queryType": "flat", "workItems": [{"id": i, "url": "u"} for i in self.ids]})
        for item_id in self.ids:
            if f"/workitems/{item_id}/comments" in url:
                if item_id in self.fail:
                    err = HTTPError(url, 500, "boom", {}, None)
                    err.read = lambda: b"server error"
                    raise err
                return _response({"count": len(COMMENTS[item_id]), "comments": COMMENTS[item_id]})
            if url.endswith(f"/_apis/wit/workitems/{item_id}?api-version=7.1"):
                return _response(ITEMS[item_id])
        raise AssertionError(f"unexpected request {req.get_method()} {url}")


def _invoke(data_dir, *args, fake=None):
    data_dir.mkdir(exist_ok=True)
    cfg = default_config()
    cfg["organization"]["url"] = ORG
    cfg["organization"]["project"] = "Proj A"
    cfg["auth"] = {"method": "pat", "pat": "fake-pat-value"}
    save_config(cfg, data_dir / "config.toml")
    fake = fake or FakeAdo()
    with patch("urllib.request.urlopen", side_effect=fake):
        result = CliRunner().invoke(main, ["mentions", *args, "--data-dir", str(data_dir)])
    return result, fake


def test_mentions_json_newest_first_read_only(tmp_path):
    data_dir = tmp_path / ".ado-search"
    result, fake = _invoke(data_dir, "--json")
    assert result.exit_code == 0, result.output
    records = json.loads(result.output)
    assert [(r["work_item_id"], r["comment_id"]) for r in records] == [(101, 11), (202, 21), (202, 24)]

    assert {r.get_method() for r in fake.requests} <= {"GET", "POST"}
    posts = [r for r in fake.requests if r.get_method() == "POST"]
    assert len(posts) == 1 and "/Proj%20A/_apis/wit/wiql" in posts[0].full_url
    assert json.loads(posts[0].data) == {"query": RECENT_MENTIONS_WIQL}
    comment_urls = sorted(r.full_url for r in fake.requests if "/comments" in r.full_url)
    assert comment_urls == [
        f"{ORG}/Proj%20A/_apis/wit/workitems/101/comments?api-version=7.1-preview.4",
        f"{ORG}/Proj%20B/_apis/wit/workitems/202/comments?api-version=7.1-preview.4",
    ]
    assert sorted(p.name for p in data_dir.iterdir()) == ["config.toml"]
    assert "fake-pat-value" not in result.output


def test_mentions_since_include_own_and_limit(tmp_path):
    result, _ = _invoke(tmp_path / ".ado-search", "--json", "--since", "2026-10-15T00:00:00Z", "--include-own")
    assert result.exit_code == 0, result.output
    assert [r["comment_id"] for r in json.loads(result.output)] == [14, 11, 21]

    result, _ = _invoke(tmp_path / ".ado-search", "--json", "--include-own", "--limit", "1")
    assert [r["comment_id"] for r in json.loads(result.output)] == [14]


def test_mentions_text_output(tmp_path):
    result, _ = _invoke(tmp_path / ".ado-search", "--since", "2026-10-16T00:00:00Z")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == ("Comments mentioning Samuel Hurley (CTR) since 2026-10-16T00:00:00Z, "
                        "newest first (1):")
    assert "#101 [Bug] Load fails (Active; Pat.Lee@co.com)" in lines
    assert "  [2026-10-16T14:55:28Z] Jane Doe (CTR) (comment id 11):" in lines
    assert "    @Samuel Hurley (CTR) please check & reply" in lines


def test_mentions_none_found(tmp_path):
    result, _ = _invoke(tmp_path / ".ado-search", fake=FakeAdo(ids=()))
    assert result.exit_code == 0, result.output
    assert "No comments mentioning Samuel Hurley (CTR) in the last 30 days." in result.output

    result, _ = _invoke(tmp_path / ".ado-search", "--json", fake=FakeAdo(ids=()))
    assert json.loads(result.output) == []


def test_mentions_warns_and_continues_when_comments_fail(tmp_path):
    result, _ = _invoke(tmp_path / ".ado-search", "--json", fake=FakeAdo(fail={202}))
    assert result.exit_code == 0, result.output
    assert "Warning: Failed to fetch comments for #202" in result.output


def test_mentions_rejects_bad_since(tmp_path):
    result, _ = _invoke(tmp_path / ".ado-search", "--since", "last week")
    assert result.exit_code == 2
    assert "not an ISO datetime" in result.output


def test_mentions_wiql_failure_exits_nonzero(tmp_path):
    class WiqlFails(FakeAdo):
        def __call__(self, req, timeout=None):
            if "/_apis/wit/wiql" in req.full_url:
                err = HTTPError(req.full_url, 400, "bad", {}, None)
                err.read = lambda: b"TF51005: unknown macro"
                raise err
            return super().__call__(req, timeout)

    result, _ = _invoke(tmp_path / ".ado-search", fake=WiqlFails())
    assert result.exit_code == 1
    assert "Failed to query @RecentMentions" in result.output
    assert "fake-pat-value" not in result.output
