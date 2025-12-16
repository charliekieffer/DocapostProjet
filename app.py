
from flask import Flask, render_template, request, Response, abort
import requests, urllib.parse, json
from datetime import date, timedelta

app = Flask(__name__)

# --- Utilitaires ---
def format_seconds_human(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:    parts.append(f"{days} day" + ("s" if days>1 else ""))
    if hours:   parts.append(f"{hours} hour" + ("s" if hours>1 else ""))
    if minutes: parts.append(f"{minutes} minute" + ("s" if minutes>1 else ""))
    return ", ".join(parts) if parts else "0 minutes"

def fetch_all_issues(base_url, auth, jql, fields, max_results=100):
    search_url = base_url.rstrip('/') + "/rest/api/2/search"
    headers = {"Accept": "application/json"}
    issues, start_at, total = [], 0, None

    while True:
        params = {
            "jql": jql,
            "fields": fields,
            "maxResults": str(max_results),
            "startAt": str(start_at),
        }
        url = search_url + "?" + urllib.parse.urlencode(params)
        resp = requests.get(url, headers=headers, auth=auth, timeout=30)
        if resp.status_code >= 400:
            abort(resp.status_code, resp.text)

        data = resp.json()
        batch = data.get("issues", [])
        issues.extend(batch)
        if total is None:
            total = data.get("total", len(batch))
        start_at += len(batch)
        if start_at >= total:
            break
    return issues

def fetch_worklogs(base_url, auth, issue_key, start_at=0, max_results=100):
    "Charge tous les worklogs d un ticket (pagination)."
    headers = {"Accept": "application/json"}
    url = base_url.rstrip('/') + f"/rest/api/2/issue/{issue_key}/worklog"
    logs_all, sa = [], start_at
    while True:
        params = {"startAt": str(sa), "maxResults": str(max_results)}
        resp = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
        if resp.status_code >= 400:
            abort(resp.status_code, f"[{issue_key}] worklog error: {resp.text}")
        data = resp.json()
        logs = data.get("worklogs", [])
        total = data.get("total", 0)
        logs_all.extend(logs)
        sa += len(logs)
        if sa >= total:
            break
    return logs_all

def project_issues(issues, base_url, auth, include_worklogs=True):
    out = []
    for it in issues:
        f = it.get("fields", {})
        original = f.get("timeoriginalestimate")     # seconds
        remaining = f.get("timeestimate")            # seconds
        spent = f.get("timespent")                   # seconds
        agg_orig = f.get("aggregatetimeoriginalestimate")
        agg_rem  = f.get("aggregatetimeestimate")
        agg_sp   = f.get("aggregatetimespent")

        item = {
            "key": it.get("key"),
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "resolution": (f.get("resolution") or {}).get("name"),
            "resolutiondate": f.get("resolutiondate"),
            "updated": f.get("updated"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "labels": f.get("labels"),
            "components": [c.get("name") for c in (f.get("components") or [])],
            "time": {
                "originalEstimateSeconds": original,
                "originalEstimateHuman":   format_seconds_human(original),
                "remainingEstimateSeconds": remaining,
                "remainingEstimateHuman":   format_seconds_human(remaining),
                "timeSpentSeconds": spent,
                "timeSpentHuman":           format_seconds_human(spent),
                "aggregateOriginalEstimateSeconds": agg_orig,
                "aggregateOriginalEstimateHuman":   format_seconds_human(agg_orig),
                "aggregateRemainingEstimateSeconds": agg_rem,
                "aggregateRemainingEstimateHuman":   format_seconds_human(agg_rem),
                "aggregateTimeSpentSeconds": agg_sp,
                "aggregateTimeSpentHuman":   format_seconds_human(agg_sp),
            }
        }

        if include_worklogs:
            logs_all = fetch_worklogs(base_url, auth, it.get("key"))
            # Worklog : timeSpentSeconds + timeSpent (humain côté API Server/DC)
            # Si timeSpent (string) absent, on reformate à partir des secondes.
            item["worklogs"] = [{
                "author": (wl.get("author") or {}).get("displayName"),
                "started": wl.get("started"),  # ISO datetime
                "timeSpentSeconds": wl.get("timeSpentSeconds"),
                "timeSpentHuman": wl.get("timeSpent") or format_seconds_human(wl.get("timeSpentSeconds")),
                "comment": wl.get("comment") if isinstance(wl.get("comment"), str) else None
            } for wl in logs_all]

        out.append(item)
    return out

@app.get("/")
def form():
    return render_template("index.html")

@app.post("/export")
def export():
    base_url   = request.form.get("baseUrl", "").strip()
    project    = request.form.get("projectKey", "").strip()
    username   = request.form.get("username", "").strip()
    password   = request.form.get("password", "")
    use_updated = request.form.get("useUpdated") == "on"

    if not all([base_url, project, username, password]):
        abort(400, "Parametres manquants")

    # JQL (updated // ou // tickets clos via resolutiondate)
    if use_updated:
        jql = (
            f"project = {project} AND statusCategory != Done "
            f"AND updated >= startOfMonth(-1) AND updated < startOfMonth() "
            f"ORDER BY updated ASC"
        )
    else:
        jql = (
            f"project = {project} AND resolution IS NOT EMPTY "
            f"AND resolutiondate >= startOfMonth(-1) AND resolutiondate < startOfMonth() "
            f"ORDER BY resolutiondate ASC"
        )

    fields = ",".join([
        "key","summary","status","resolution","resolutiondate","updated",
        "assignee","labels","components",
        # time tracking (issue + aggregates)
        "timeoriginalestimate","timeestimate","timespent",
        "aggregatetimeoriginalestimate","aggregatetimeestimate","aggregatetimespent"
    ])

    issues = fetch_all_issues(base_url, (username, password), jql, fields)
    projected = project_issues(issues, base_url, (username, password), include_worklogs=True)

    payload = json.dumps(projected, ensure_ascii=False, indent=2)
    filename = f"jira_{project}_prev_month{'_updated' if use_updated else ''}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    )

@app.get("/ping")
def ping():
    return "pong", 200

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)
