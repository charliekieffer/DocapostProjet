#!/usr/bin/env python3
"""
get_jira.py — Récupération des tickets Jira (Jira Server/DC) via JQL

Fonctions :
- Authentification Basic (login/mot de passe) ou via variables d'environnement
- Encodage propre des paramètres (JQL, fields)
- Pagination automatique (startAt/maxResults)
- Export JSON

Utilisation :
  python get_jira.py \
    --base-url https://jira.zecarte.fr:8443 \
    --project ARAPS \
    --output jira_ARAPS_prev_month.json \
    [--use-updated] \
    [--username c.kieffer] [--password '***']

Par défaut : filtre sur resolutiondate (tickets réellement clos N-1).
Option --use-updated : utilise updated (moins strict) + status != Open.
"""

import argparse
import json
import sys
from typing import List, Dict, Any
import getpass
import urllib.parse
import requests


def build_search_url(base_url: str) -> str:
    base = base_url.rstrip('/')
    return f"{base}/rest/api/2/search"


def fetch_all_issues(search_url: str, auth: tuple, jql: str, fields: str,
                     max_results: int = 100) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    start_at = 0
    total = None

    headers = {"Accept": "application/json"}

    while True:
        params = {
            "jql": jql,
            "fields": fields,
            "maxResults": str(max_results),
            "startAt": str(start_at),
        }
        # Construire l'URL avec encodage propre
        url = search_url + "?" + urllib.parse.urlencode(params)
        resp = requests.get(url, headers=headers, auth=auth)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            sys.stderr.write(f"HTTP error {resp.status_code}: {resp.text}\n")
            raise e
        data = resp.json()
        batch = data.get("issues", [])
        issues.extend(batch)
        if total is None:
            total = data.get("total", len(batch))
        start_at += len(batch)
        if start_at >= total:
            break
    return issues


def project_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in issues:
        f = it.get("fields", {})
        out.append({
            "key": it.get("key"),
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "resolution": (f.get("resolution") or {}).get("name"),
            "resolutiondate": f.get("resolutiondate"),
            "updated": f.get("updated"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "labels": f.get("labels"),
            "components": [c.get("name") for c in (f.get("components") or [])],
        })
    return out


def main():
    parser = argparse.ArgumentParser(description="Récupère des tickets Jira via JQL et exporte en JSON")
    parser.add_argument("--base-url", required=True, help="Base URL de Jira, ex: https://jira.zecarte.fr:8443")
    parser.add_argument("--project", required=True, help="Clé projet (ex: ARAPS)")
    parser.add_argument("--output", required=True, help="Chemin fichier de sortie JSON")
    parser.add_argument("--username", help="Login Jira (sinon invite)")
    parser.add_argument("--password", help="Mot de passe Jira (sinon invite masquée)")
    parser.add_argument("--use-updated", action="store_true",
                        help="Utilise 'updated' sur N-1 au lieu de 'resolutiondate' (moins strict)")

    args = parser.parse_args()

    username = args.username or input("Login Jira: ")
    password = args.password or getpass.getpass("Mot de passe Jira (masqué): ")

    # JQL
    if args.use_updated:
        jql = (
            f"project = {args.project} AND status NOT IN (Open) "
            f"AND updated >= startOfMonth(-1) AND updated < startOfMonth() "
            f"ORDER BY updated ASC"
        )
    else:
        jql = (
            f"project = {args.project} AND resolution IS NOT EMPTY "
            f"AND resolutiondate >= startOfMonth(-1) AND resolutiondate < startOfMonth() "
            f"ORDER BY resolutiondate ASC"
        )

    fields = ",".join([
        "key","summary","status","resolution","resolutiondate",
        "updated","assignee","labels","components"
    ])

    search_url = build_search_url(args.base_url)
    issues = fetch_all_issues(search_url, (username, password), jql, fields)
    projected = project_issues(issues)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(projected, f, ensure_ascii=False, indent=2)

    print(f"OK — {len(projected)} tickets exportés vers {args.output}")


if __name__ == "__main__":
    main()
