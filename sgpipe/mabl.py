"""mabl API client. Stdlib only.

Auth is basic auth with the literal username "key" and the API key as the
password. Note the endpoint asymmetry, which is real and not a typo here:
reading scenarios is GET /dataTables/scenarios?data_table_id=..., while
writing them is PUT /dataTables/{id}/scenarios.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.mabl.com"


class MablError(RuntimeError):
    def __init__(self, message, status=None):
        RuntimeError.__init__(self, message)
        self.status = status


class Mabl(object):
    def __init__(self, api_key, workspace_id, base=BASE):
        self.workspace_id = workspace_id
        self.base = base.rstrip("/")
        token = base64.b64encode(("key:%s" % api_key).encode()).decode()
        self._auth = "Basic %s" % token

    # ---- transport -------------------------------------------------------
    def _call(self, method, path, params=None, body=None, tolerate=()):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in tolerate:
                raise
            detail = exc.read().decode()[:600]
            raise MablError(
                "%s %s -> %s %s\n%s" % (method, path, exc.code, exc.reason, detail),
                status=exc.code)
        except urllib.error.URLError as exc:
            raise MablError("%s %s -> %s" % (method, path, exc.reason))

    def _paged(self, path, params, key, max_pages=200):
        params = dict(params or {})
        params.setdefault("limit", 100)
        items, cursor, seen = [], None, set()
        for _ in range(max_pages):
            if cursor:
                params["cursor"] = cursor
            page = self._call("GET", path, params=params)
            batch = page.get(key) or []
            items.extend(batch)
            cursor = page.get("cursor") or page.get("nextCursor")
            # A cursor can come back even on the last page, so stop on an empty
            # batch or a repeated cursor rather than trusting its absence.
            if not cursor or not batch or cursor in seen:
                return items
            seen.add(cursor)
        print("  ! stopped paging %s after %d pages" % (path, max_pages))
        return items

    # ---- data tables -----------------------------------------------------
    def list_tables(self):
        return self._paged("/dataTables", {"workspace_id": self.workspace_id}, "dataTables")

    def find_table(self, name):
        """First table with this exact name, or None. There is no server-side
        name filter, so this matches client-side."""
        matches = [t for t in self.list_tables() if t.get("name") == name]
        if len(matches) > 1:
            ids = ", ".join(t["id"] for t in matches)
            print("  ! %d tables share the name %r (%s) - using the first" % (len(matches), name, ids))
        return matches[0] if matches else None

    def create_table(self, name, description="", scenarios=None):
        body = {
            "workspace_id": self.workspace_id,
            "data_table": {"name": name, "description": description[:255]},
            "scenarios": scenarios or [],
        }
        return self._call("POST", "/dataTables", body=body)

    def resolve_table(self, name, description=""):
        """Return an existing table id or create one. Called once per run;
        never inside a loop - POST /dataTables always creates a NEW table and
        never upserts on name, which is what produces duplicate tables."""
        found = self.find_table(name)
        if found:
            return found["id"], False
        return self.create_table(name, description)["id"], True

    def get_scenarios(self, table_id):
        try:
            return self._paged(
                "/dataTables/scenarios", {"data_table_id": table_id}, "scenarios"
            )
        except MablError as exc:
            # Documented shape is the query-param form above; fall back to the
            # path form only if that route is genuinely absent. Anything else
            # (401, 403) must surface rather than be retried into confusion.
            if exc.status not in (404, 405):
                raise
            return self._paged("/dataTables/%s/scenarios" % table_id, {}, "scenarios")

    def get_columns(self, table_id):
        res = self._call("GET", "/dataTables/%s/variableNames" % table_id)
        if isinstance(res, list):
            return res
        return res.get("variableNames") or res.get("names") or []

    def add_column(self, table_id, name, default_value=""):
        return self._call(
            "POST",
            "/dataTables/%s/variableNames" % table_id,
            body={"name": name, "default_value": default_value},
        )

    def ensure_columns(self, table_id, wanted):
        """Create any column the table is missing. Adding a variable appends it
        to every scenario with an empty default, so this is safe to re-run."""
        try:
            existing = set(self.get_columns(table_id))
        except MablError as exc:
            if exc.status not in (404, 405):
                raise
            print("  ! no variableNames route - relying on reconcile to carry columns")
            return []
        missing = [c for c in wanted if c not in existing]
        for col in missing:
            self.add_column(table_id, col)
        return missing

    def reconcile_scenarios(self, table_id, scenarios):
        """Make the table match `scenarios` EXACTLY. Any row omitted here is
        deleted, so always pass the complete desired set."""
        res = self._call(
            "PUT", "/dataTables/%s/scenarios" % table_id, body={"scenarios": scenarios}
        )
        return res.get("scenarios") or []

    # ---- execution -------------------------------------------------------
    def deployment_event(self, application_id, environment_id, plan_labels,
                         revision=None, preview=False):
        body = {
            "application_id": application_id,
            "environment_id": environment_id,
            "plan_labels": list(plan_labels),
        }
        if revision:
            body["revision"] = revision
        params = {"preview": "true"} if preview else None
        return self._call("POST", "/events/deployment", params=params, body=body)

    def event_result(self, event_id):
        return self._call("GET", "/execution/result/event/%s" % event_id)

    def wait_for_event(self, event_id, interval=15, timeout=1200):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.event_result(event_id)
            metrics = last.get("plan_execution_metrics") or {}
            running = (metrics.get("running") or 0) + (metrics.get("queued") or 0)
            total = metrics.get("total") or 0
            done = total and not running
            print("    plans: %s total, %s passed, %s failed, %s running"
                  % (total, metrics.get("passed", 0), metrics.get("failed", 0), running))
            if done:
                return last
            time.sleep(interval)
        print("  ! timed out after %ss" % timeout)
        return last
