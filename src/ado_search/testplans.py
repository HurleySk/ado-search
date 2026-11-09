"""Live reads of Azure Test Plans: plans, suites, test cases, points, runs and results."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from urllib.parse import quote

import click

from ado_search.auth import (
    OP_TEST_PLAN_LIST, OP_TEST_POINT_LIST, OP_TEST_RESULT_LIST, OP_TEST_RUN_LIST,
    OP_TEST_SUITE_CASES, OP_TEST_SUITE_LIST,
)
from ado_search.markdown import strip_html

MAX_PAGES = 500
SKIP_PAGE = 100

DEFAULT_CASE_FIELDS = (
    "System.Title",
    "System.State",
    "System.CreatedBy",
    "System.CreatedDate",
    "System.AssignedTo",
    "System.AreaPath",
    "System.Tags",
    "Microsoft.VSTS.Common.Priority",
    "Microsoft.VSTS.TCM.AutomationStatus",
    "Microsoft.VSTS.TCM.Steps",
    "Microsoft.VSTS.TCM.Parameters",
)


class Conn:
    """Connection details for one live call."""

    def __init__(self, *, org: str, project: str, auth_method: str, pat: str = ""):
        self.org, self.project, self.auth_method, self.pat = org, project, auth_method, pat

    async def call(self, operation: str, **kwargs):
        from ado_search.runner import run_operation_full
        result = await run_operation_full(
            self.auth_method, operation, org=self.org, project=self.project, pat=self.pat, **kwargs,
        )
        if result.returncode != 0:
            raise click.ClickException(result.stderr or f"{operation} failed")
        return (json.loads(result.stdout) if result.stdout else {}), result.headers


async def _all_by_token(conn: Conn, operation: str, params: list[str] | None = None, **ids) -> list[dict]:
    """Follow x-ms-continuationtoken until ADO stops sending one."""
    items: list[dict] = []
    token = None
    for _ in range(MAX_PAGES):
        page_params = list(params or [])
        if token:
            page_params.append(f"continuationToken={quote(token, safe='')}")
        data, headers = await conn.call(operation, params=page_params, **ids)
        items.extend(data.get("value", []) if isinstance(data, dict) else data)
        token = headers.get("x-ms-continuationtoken")
        if not token:
            return items
    raise click.ClickException(f"{operation}: more than {MAX_PAGES} pages")


async def _all_by_skip(conn: Conn, operation: str, params: list[str] | None = None,
                       limit: int | None = None, **ids) -> list[dict]:
    """Page with $top/$skip until a short page (or ``limit`` items)."""
    items: list[dict] = []
    for page in range(MAX_PAGES):
        page_params = [*(params or []), f"$top={SKIP_PAGE}", f"$skip={page * SKIP_PAGE}"]
        data, _ = await conn.call(operation, params=page_params, **ids)
        batch = data.get("value", []) if isinstance(data, dict) else data
        items.extend(batch)
        if len(batch) < SKIP_PAGE or (limit and len(items) >= limit):
            return items[:limit] if limit else items
    raise click.ClickException(f"{operation}: more than {MAX_PAGES} pages")


def _name(identity: Any) -> str | None:
    """Display name of an identity (plain ref, or the returnIdentityRef wrapper)."""
    if isinstance(identity, dict):
        if isinstance(identity.get("identityRef"), dict):
            identity = identity["identityRef"]
        return (identity.get("displayName") or identity.get("uniqueName")
                or identity.get("distinctDisplayName"))
    return identity or None


def _ref_id(ref: Any) -> Any:
    return ref.get("id") if isinstance(ref, dict) else None


def _date(value: Any) -> str | None:
    """YYYY-MM-DD, or None for empty values and ADO's 0001-01-01 placeholder."""
    day = (value or "")[:10]
    return None if not day or day.startswith("0001-") else day


# ---------------------------------------------------------------- plans

def plan_record(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "state": p.get("state"),
        "owner": _name(p.get("owner")),
        "area": p.get("areaPath"),
        "iteration": p.get("iteration"),
        "start": _date(p.get("startDate")),
        "end": _date(p.get("endDate")),
        "root_suite_id": _ref_id(p.get("rootSuite")),
        "updated": _date(p.get("updatedDate")),
        "updated_by": _name(p.get("updatedBy")),
        "description": strip_html(p.get("description") or "") or None,
    }


async def list_plans(conn: Conn, *, owner: str | None = None, active: bool = False) -> list[dict]:
    plans = [plan_record(p) for p in await _all_by_token(conn, OP_TEST_PLAN_LIST,
                                                         ["filterActivePlans=true"] if active else [])]
    if owner:
        needle = owner.casefold()
        plans = [p for p in plans if needle in (p["owner"] or "").casefold()]
    return sorted(plans, key=lambda p: p["id"] or 0)


def format_plans(plans: list[dict]) -> str:
    lines = [f"{len(plans)} test plan(s)"]
    for p in plans:
        lines.append(f"  {p['id']}  [{p['state']}]  {p['name']}")
        lines.append(f"      owner {p['owner']} | area {p['area']} | iteration {p['iteration']} | "
                     f"{p['start']}..{p['end']} | root suite {p['root_suite_id']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- suites

def suite_record(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "type": s.get("suiteType"),
        "parent_id": _ref_id(s.get("parentSuite")),
        "requirement_id": s.get("requirementId") or None,
        "query": s.get("queryString") or None,
        "default_testers": [_name(t) for t in s.get("defaultTesters") or []] or None,
        "updated": _date(s.get("lastUpdatedDate")),
        "updated_by": _name(s.get("lastUpdatedBy")),
    }


def order_suites(suites: list[dict]) -> list[dict]:
    """Depth-first order with ``depth`` and ``path`` filled in, children in the order ADO lists them."""
    by_parent: dict[Any, list[dict]] = {}
    ids = {s["id"] for s in suites}
    for s in suites:
        parent = s["parent_id"] if s["parent_id"] in ids else None
        by_parent.setdefault(parent, []).append(s)
    ordered: list[dict] = []

    def walk(parent, depth, prefix):
        for s in by_parent.get(parent, []):
            path = f"{prefix}/{s['name']}" if prefix else s["name"]
            ordered.append({**s, "depth": depth, "path": path})
            walk(s["id"], depth + 1, path)

    walk(None, 0, "")
    return ordered


async def list_suites(conn: Conn, plan_id: int) -> list[dict]:
    raw = await _all_by_token(conn, OP_TEST_SUITE_LIST, plan_id=plan_id)
    return order_suites([suite_record(s) for s in raw])


def format_suites(plan_id: int, suites: list[dict]) -> str:
    lines = [f"Plan {plan_id}: {len(suites)} suite(s)"]
    for s in suites:
        kind = {"staticTestSuite": "static", "requirementTestSuite": "requirement",
                "dynamicTestSuite": "query"}.get(s["type"] or "", s["type"])
        extra = f" -> #{s['requirement_id']}" if s["requirement_id"] else ""
        if s["query"]:
            extra += f" | {s['query']}"
        lines.append(f"  {'  ' * s['depth']}{s['id']}  {s['name']}  ({kind}{extra})")
    return "\n".join(lines)


# ---------------------------------------------------------------- test cases

def parse_steps(xml: str | None) -> list[dict]:
    """Turn Microsoft.VSTS.TCM.Steps XML into ordered action/expected pairs.

    A shared-step reference (``compref``) becomes ``{"shared_steps": id}``;
    any steps nested under it follow it in order.
    """
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [{"raw": xml}]
    steps: list[dict] = []

    def walk(node):
        for child in node:
            if child.tag == "step":
                parts = [strip_html(p.text or "").strip() for p in child.findall("parameterizedString")]
                parts += [""] * (2 - len(parts))
                steps.append({"action": parts[0], "expected": parts[1] or None})
            elif child.tag == "compref":
                steps.append({"shared_steps": int(child.get("ref", "0") or 0)})
                walk(child)

    walk(root)
    return steps


def parse_parameters(xml: str | None) -> list[str]:
    if not xml:
        return []
    try:
        return [p.get("name", "") for p in ET.fromstring(xml).iter("param")]
    except ET.ParseError:
        return []


def _wit_fields(work_item: dict) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for entry in work_item.get("workItemFields") or []:
        if isinstance(entry, dict):
            fields.update(entry)
    return fields


def case_record(c: dict, field_names: Iterable[str]) -> dict:
    wi = c.get("workItem") or {}
    raw = {k.casefold(): v for k, v in _wit_fields(wi).items()}
    fields = {}
    for name in field_names:
        value = raw.get(name.casefold())
        if name == "Microsoft.VSTS.TCM.Steps":
            value = parse_steps(value)
        elif name == "Microsoft.VSTS.TCM.Parameters":
            value = parse_parameters(value)
        else:
            value = _name(value) if isinstance(value, dict) else value
        fields[name] = value
    return {
        "id": wi.get("id"),
        "title": wi.get("name") or fields.get("System.Title"),
        "order": c.get("order"),
        "assignments": [
            {"configuration": (a.get("configurationName") or _name(a.get("configuration"))),
             "tester": _name(a.get("tester"))}
            for a in c.get("pointAssignments") or []
        ],
        "fields": fields,
    }


async def list_suite_cases(conn: Conn, plan_id: int, suite_id: int,
                           fields: Iterable[str] | None = None) -> list[dict]:
    names = list(fields) if fields else list(DEFAULT_CASE_FIELDS)
    params = [f"witFields={quote(','.join(names), safe=',')}"]
    raw = await _all_by_token(conn, OP_TEST_SUITE_CASES, params, plan_id=plan_id, suite_id=suite_id)
    return [case_record(c, names) for c in raw]


def format_cases(plan_id: int, suite_id: int, cases: list[dict]) -> str:
    lines = [f"Plan {plan_id} suite {suite_id}: {len(cases)} test case(s)"]
    for c in cases:
        f = c["fields"]
        lines.append(f"\n  #{c['id']}  {c['title']}")
        meta = {k.split(".")[-1]: v for k, v in f.items()
                if k not in ("System.Title", "Microsoft.VSTS.TCM.Steps") and v not in (None, "", [])}
        if meta:
            lines.append("      " + " | ".join(f"{k} {v}" for k, v in meta.items()))
        for a in c["assignments"]:
            lines.append(f"      point: {a['configuration']} -> {a['tester']}")
        for i, s in enumerate(f.get("Microsoft.VSTS.TCM.Steps") or [], 1):
            if "shared_steps" in s:
                lines.append(f"      {i}. [shared steps #{s['shared_steps']}]")
            elif "raw" in s:
                lines.append(f"      (unparsed steps) {s['raw'][:200]}")
            else:
                lines.append(f"      {i}. {s['action']}")
                if s["expected"]:
                    lines.append(f"         => {s['expected']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- points

def point_record(p: dict) -> dict:
    results = p.get("results") or {}
    last = results.get("lastResultDetails") or {}
    case = p.get("testCaseReference") or {}
    return {
        "id": p.get("id"),
        "test_case_id": case.get("id"),
        "test_case": case.get("name"),
        "configuration": (p.get("configuration") or {}).get("name"),
        "tester": _name(p.get("tester")),
        "outcome": results.get("outcome"),
        "state": results.get("lastResultState") or results.get("state"),
        "last_run_id": results.get("lastTestRunId") or None,
        "last_run_date": _date(last.get("dateCompleted")),
        "run_by": _name(last.get("runBy")),
        "active": p.get("isActive"),
    }


async def list_points(conn: Conn, plan_id: int, suite_id: int) -> list[dict]:
    raw = await _all_by_token(conn, OP_TEST_POINT_LIST, plan_id=plan_id, suite_id=suite_id)
    return [point_record(p) for p in raw]


def format_points(plan_id: int, suite_id: int, points: list[dict]) -> str:
    lines = [f"Plan {plan_id} suite {suite_id}: {len(points)} test point(s)"]
    for p in points:
        lines.append(f"  {p['id']}  #{p['test_case_id']} {p['test_case']}  [{p['configuration']}] "
                     f"tester {p['tester']} | {p['outcome']} | run {p['last_run_id']} "
                     f"{p['last_run_date']} by {p['run_by']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- runs and results

def run_record(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "state": r.get("state"),
        "automated": r.get("isAutomated"),
        "owner": _name(r.get("owner")),
        "plan_id": _ref_id(r.get("plan")),
        "started": _date(r.get("startedDate")),
        "completed": _date(r.get("completedDate")),
        "total": r.get("totalTests"),
        "passed": r.get("passedTests"),
        "not_applicable": r.get("notApplicableTests"),
        "incomplete": r.get("incompleteTests"),
        "unanalyzed": r.get("unanalyzedTests"),
        "comment": r.get("comment") or None,
    }


async def list_runs(conn: Conn, plan_id: int | None = None, limit: int | None = None) -> list[dict]:
    params = [f"planId={plan_id}"] if plan_id else []
    return [run_record(r) for r in await _all_by_skip(conn, OP_TEST_RUN_LIST, params, limit=limit)]


def format_runs(runs: list[dict]) -> str:
    lines = [f"{len(runs)} test run(s)"]
    for r in runs:
        auto = "automated" if r["automated"] else "manual"
        lines.append(f"  {r['id']}  {r['name']}  [{r['state']}, {auto}] plan {r['plan_id']} | "
                     f"{r['started']}..{r['completed']} by {r['owner']} | "
                     f"{r['passed']}/{r['total']} passed, {r['unanalyzed']} unanalyzed, "
                     f"{r['not_applicable']} n/a, {r['incomplete']} incomplete")
    return "\n".join(lines)


def result_record(r: dict) -> dict:
    case = r.get("testCase") or {}
    return {
        "id": r.get("id"),
        "test_case_id": int(case["id"]) if str(case.get("id", "")).isdigit() else case.get("id"),
        "test_case": case.get("name") or r.get("testCaseTitle"),
        "outcome": r.get("outcome"),
        "state": r.get("state"),
        "configuration": (r.get("configuration") or {}).get("name"),
        "run_by": _name(r.get("runBy")),
        "completed": _date(r.get("completedDate")),
        "comment": r.get("comment") or None,
        "error": r.get("errorMessage") or None,
        "bugs": [int(b["id"]) for b in r.get("associatedBugs") or [] if str(b.get("id", "")).isdigit()],
    }


async def list_results(conn: Conn, run_id: int) -> list[dict]:
    return [result_record(r) for r in await _all_by_skip(conn, OP_TEST_RESULT_LIST, run_id=run_id)]


def format_results(run_id: int, results: list[dict]) -> str:
    lines = [f"Run {run_id}: {len(results)} result(s)"]
    for r in results:
        bugs = f" | bugs {', '.join(f'#{b}' for b in r['bugs'])}" if r["bugs"] else ""
        note = f" | {r['comment'] or r['error']}" if (r["comment"] or r["error"]) else ""
        lines.append(f"  #{r['test_case_id']} {r['test_case']}  [{r['configuration']}] "
                     f"{r['outcome']} by {r['run_by']} {r['completed']}{bugs}{note}")
    return "\n".join(lines)
