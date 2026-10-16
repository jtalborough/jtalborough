import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USER = "jtalborough"
README = Path(__file__).with_name("README.md")
HISTORY = Path(__file__).parent / "data" / "feed-history.json"
CHART = Path(__file__).parent / "data" / "activity.svg"
START = "<!-- FEED:START -->"
END = "<!-- FEED:END -->"
LIMIT = 8
SKIP = {f"{USER}/{USER}"}
API = "https://api.github.com"
WEEKS = 12
COMMIT_CAP = 2000
TECH_ORDER = (
    "Ansible",
    "k3s",
    "Kubernetes",
    "Helm",
    "Docker",
    "Fleet",
    "GitHub Actions",
    "Python",
    "Flask",
    "TypeScript",
    "React",
    "PostgreSQL",
    "C#/.NET",
)
INFRA = {"Ansible", "k3s", "Kubernetes", "Helm", "Docker", "Fleet", "GitHub Actions"}
APP = {"Python", "Flask", "TypeScript", "React", "PostgreSQL"}
THEMES = {
    "infra automation",
    "application services",
    "control software",
    "automation and services",
}
DEPS = {
    "flask": "Flask",
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "psycopg2-binary": "PostgreSQL",
    "psycopg-binary": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "pg": "PostgreSQL",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "npgsql": "PostgreSQL",
    "react": "React",
    "typescript": "TypeScript",
}
SAFE_LANGUAGE = re.compile(r"[A-Za-z0-9#+.\- ]{1,32}")
TEMPLATE_WORDS = {
    "private",
    "work",
    "last",
    "days",
    "commit",
    "commits",
    "across",
    "active",
    "repos",
    "languages",
    "tech",
    "mix",
    "this",
    "week",
    "contributions",
    "past",
    "weeks",
    "theme",
    "over",
    "the",
}


def main():
    text = README.read_text()
    if START not in text or END not in text or text.find(START) > text.find(END):
        raise SystemExit("feed markers missing")
    body, files = collect_all()
    updated = splice(text, body)
    if updated != text:
        README.write_text(updated)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)


def collect_all():
    source = os.environ.get("FEED_SOURCE", "github")
    if source != "github":
        raise SystemExit(f"unknown FEED_SOURCE: {source}")
    public = github_activity()
    private_md, files = private_output()
    body = "\n".join(public) if public else "No recent public activity."
    if private_md:
        body = f"{body}\n\n{private_md.strip()}"
    return body, files


def splice(text, body):
    pre, rest = text.split(START, 1)
    _, post = rest.split(END, 1)
    return f"{pre}{START}\n{body.strip()}\n{END}{post}"


def github_activity():
    events = fetch_json(f"{API}/users/{USER}/events/public?per_page=40")
    if not isinstance(events, list):
        raise SystemExit("unexpected events payload")
    events.sort(key=lambda event: event.get("created_at") or "", reverse=True)
    chosen = []
    seen = set()
    for event in events:
        key = event_key(event)
        if key is None or key in seen:
            continue
        seen.add(key)
        chosen.append(event)
        if len(chosen) == LIMIT:
            break
    return [line for event in chosen if (line := format_event(event))]


def event_key(event):
    repo = (event.get("repo") or {}).get("name") or ""
    if not repo or repo in SKIP:
        return None
    kind = event.get("type")
    payload = event.get("payload") or {}
    if kind == "PushEvent":
        return ("push", repo)
    if kind == "PullRequestEvent":
        return ("pr", repo, payload.get("number"))
    if kind == "IssuesEvent":
        return ("issue", repo, (payload.get("issue") or {}).get("number"))
    if kind == "ReleaseEvent":
        release = payload.get("release") or {}
        return ("release", repo, release.get("tag_name") or release.get("id"))
    if kind == "CreateEvent" and payload.get("ref_type") == "repository":
        return ("repo", repo)
    return None


def format_event(event):
    kind = event["type"]
    repo = event["repo"]["name"]
    payload = event["payload"]
    date_label = event["created_at"][:10]
    name = repo.split("/", 1)[-1]
    if kind == "PushEvent":
        sha = payload.get("head") or ""
        subject = commit_subject(repo, sha) if sha else ""
        link = f"{repo_url(repo)}/commit/{sha}" if sha else repo_url(repo)
        detail = f" — {plain(subject)}" if subject else ""
        return f"- Pushed to [{name}]({link}){detail} — {date_label}"
    if kind == "PullRequestEvent":
        number = payload.get("number")
        pr = fetch_json(f"{API}/repos/{repo}/pulls/{number}")
        title = plain(pr.get("title") or "")
        action = payload.get("action") or ""
        if action == "closed" and pr.get("merged"):
            verb = "Merged"
        elif action == "closed":
            verb = "Closed"
        elif action == "opened":
            verb = "Opened"
        elif action == "reopened":
            verb = "Reopened"
        else:
            verb = action[:1].upper() + action[1:] if action else "Updated"
        url = pr.get("html_url") or f"{repo_url(repo)}/pull/{number}"
        return f"- {verb} [{name}#{number}]({url}) — {title} — {date_label}"
    if kind == "IssuesEvent":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = plain(issue.get("title") or "")
        action = payload.get("action") or "updated"
        verb = action[:1].upper() + action[1:]
        url = issue.get("html_url") or f"{repo_url(repo)}/issues/{number}"
        return f"- {verb} [{name}#{number}]({url}) — {title} — {date_label}"
    if kind == "ReleaseEvent":
        release = payload.get("release") or {}
        tag = plain(release.get("tag_name") or "release")
        url = release.get("html_url") or repo_url(repo)
        return f"- Released [{name} {tag}]({url}) — {date_label}"
    if kind == "CreateEvent":
        return f"- Created [{name}]({repo_url(repo)}) — {date_label}"
    return None


def commit_subject(repo, sha):
    commit = fetch_json(f"{API}/repos/{repo}/commits/{sha}")
    message = ((commit.get("commit") or {}).get("message")) or ""
    return message.splitlines()[0] if message else ""


def repo_url(name):
    return f"https://github.com/{name}"


def plain(text):
    return " ".join(text.replace("]", ")").split())


def fetch_json(url):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    data, _ = fetch(url, token)
    return data


def private_output():
    token = os.environ.get("PRIVATE_FEED_TOKEN", "").strip()
    if not token:
        print("private section skipped")
        return "", {}
    try:
        return build_private(token)
    except Exception:
        print("private section skipped")
        return "", {}


def build_private(token):
    today = utc_today()
    history = load_history(HISTORY)
    snapshot, banned, counts = scan_private(token, today)
    unit = "commit"
    if counts is None and not any(row["commits"] for row in history["weeks"]):
        counts = backfill_counts(token, today)
        unit = "contribution"
    if counts is not None:
        apply_weeks(history, counts)
    if snapshot is None:
        snapshot = latest_snapshot(history)
    if snapshot is None:
        raise RuntimeError("private")
    snapshot = scrub_snapshot(snapshot, banned)
    snapshot["theme"] = theme_for(snapshot["tech"])
    if snapshot["theme"].lower() in {name.lower() for name in banned if name}:
        snapshot["theme"] = ""
    upsert_snapshot(history, snapshot)
    rows = week_rows(history, today)
    markdown = render_private(latest_snapshot(history), rows, unit)
    files = {HISTORY: dump_history(history)}
    svg = ""
    if series_complete(rows):
        svg = activity_svg([(row["week"], row["commits"]) for row in rows], unit)
        files[CHART] = svg
    blob = "\n".join((markdown, dump_history(history), svg))
    if leaks(blob, banned):
        raise RuntimeError("sanitizer")
    return markdown, files


def scan_private(token, today):
    now = datetime.now(timezone.utc)
    since_30 = now - timedelta(days=30)
    since_7 = now - timedelta(days=7)
    since_window = datetime.combine(window_start(today), datetime.min.time(), tzinfo=timezone.utc)
    commits_7 = commits_30 = repos_7 = repos_30 = 0
    languages = {}
    tech = set()
    banned = set()
    weeks = {week: 0 for week in recent_week_starts(today)}
    counted = 0
    failed = 0
    for repo in list_private_repos(token):
        full_name = repo.get("full_name") or ""
        owner = ((repo.get("owner") or {}).get("login")) or ""
        name = repo.get("name") or ""
        if full_name:
            banned.add(full_name)
        if name:
            banned.add(name)
        if owner and owner != USER:
            banned.add(owner)
        try:
            times = authored_times(token, full_name, since_window)
        except Exception:
            failed += 1
            continue
        counted += 1
        got_7, got_30, by_week = fold_times(times, since_7, since_30, weeks)
        for week, count in by_week.items():
            weeks[week] += count
        commits_7 += got_7
        commits_30 += got_30
        if got_7:
            repos_7 += 1
        if got_30:
            repos_30 += 1
        if got_30 <= 0:
            continue
        try:
            for language, count in repo_languages(token, full_name).items():
                languages[language] = languages.get(language, 0) + count
            found, paths = repo_tech(token, repo)
            tech.update(found)
            banned.update(paths)
        except Exception:
            continue
    if languages.get("Python"):
        tech.add("Python")
    if languages.get("TypeScript"):
        tech.add("TypeScript")
    if languages.get("C#"):
        tech.add("C#/.NET")
    snapshot = {
        "date": today.isoformat(),
        "week": week_start(today).isoformat(),
        "commits_7d": commits_7,
        "commits_30d": commits_30,
        "repos_7d": repos_7,
        "repos_30d": repos_30,
        "languages": language_shares(languages),
        "tech": ordered_tech(tech),
        "theme": theme_for(tech),
    }
    if counted == 0 and failed:
        return None, banned, None
    return snapshot, banned, weeks


def list_private_repos(token):
    url = (
        f"{API}/user/repos?visibility=private&per_page=100"
        "&affiliation=owner,collaborator,organization_member"
    )
    repos = []
    while url and len(repos) < 100:
        page, url = fetch(url, token)
        if not isinstance(page, list):
            break
        for repo in page:
            if repo.get("private") and not repo.get("fork") and not repo.get("archived"):
                repos.append(repo)
    return repos


def authored_times(token, full_name, since):
    quoted = urllib.parse.quote(full_name, safe="/")
    stamp = urllib.parse.quote(since.strftime("%Y-%m-%dT%H:%M:%SZ"), safe="")
    url = f"{API}/repos/{quoted}/commits?author={USER}&since={stamp}&per_page=100"
    times = []
    while url and len(times) < COMMIT_CAP:
        page, url = fetch(url, token)
        if not isinstance(page, list) or not page:
            break
        for item in page:
            when = parse_utc(((item.get("commit") or {}).get("author") or {}).get("date"))
            if when is not None:
                times.append(when)
            if len(times) >= COMMIT_CAP:
                break
        del page
    return times


def fold_times(times, since_7, since_30, week_keys):
    got_7 = got_30 = 0
    weeks = {week: 0 for week in week_keys}
    for when in times:
        if when >= since_7:
            got_7 += 1
        if when >= since_30:
            got_30 += 1
        week = week_start(when.date()).isoformat()
        if week in weeks:
            weeks[week] += 1
    return got_7, got_30, weeks


def parse_utc(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def repo_languages(token, full_name):
    quoted = urllib.parse.quote(full_name, safe="/")
    data, _ = fetch(f"{API}/repos/{quoted}/languages", token)
    if not isinstance(data, dict):
        return {}
    counts = {}
    for name, count in data.items():
        if SAFE_LANGUAGE.fullmatch(str(name)) and isinstance(count, int):
            counts[str(name)] = count
    return counts


def repo_tech(token, repo):
    full_name = repo.get("full_name") or ""
    branch = repo.get("default_branch") or "main"
    quoted = urllib.parse.quote(full_name, safe="/")
    ref = urllib.parse.quote(branch, safe="")
    data, _ = fetch(f"{API}/repos/{quoted}/git/trees/{ref}?recursive=1", token)
    paths = []
    if isinstance(data, dict):
        for node in data.get("tree") or []:
            path = node.get("path") or ""
            if path and node.get("type") == "blob":
                paths.append(path)
    tech = tech_from_paths(paths)
    manifests = [
        path
        for path in paths
        if path.rsplit("/", 1)[-1].lower() in {"package.json", "pyproject.toml", "requirements.txt"}
    ][:6]
    for path in manifests:
        try:
            text = read_text(token, full_name, path, branch)
        except Exception:
            continue
        tech.update(tech_from_manifest(text))
        del text
    if language_present(paths, (".py",)):
        tech.add("Python")
    if language_present(paths, (".ts", ".tsx")):
        tech.add("TypeScript")
    if language_present(paths, (".cs",)):
        tech.add("C#/.NET")
    return tech, paths


def language_present(paths, suffixes):
    return any(path.lower().endswith(suffixes) for path in paths)


def tech_from_paths(paths):
    found = set()
    for path in paths:
        lowered = path.lower()
        name = lowered.rsplit("/", 1)[-1]
        if (
            name in {"ansible.cfg", "site.yml", "site.yaml", "playbook.yml", "playbook.yaml"}
            or "/playbooks/" in f"/{lowered}"
        ):
            found.add("Ansible")
        if name.startswith("k3s") or "/k3s/" in f"/{lowered}":
            found.add("k3s")
        if name in {"kustomization.yml", "kustomization.yaml", "chart.yaml"} or "/k8s/" in f"/{lowered}" or "/kubernetes/" in f"/{lowered}":
            found.add("Kubernetes")
        if name == "chart.yaml":
            found.add("Helm")
        if name == "dockerfile" or name.startswith("dockerfile.") or name in {"docker-compose.yml", "docker-compose.yaml"}:
            found.add("Docker")
        if name == "fleet.yaml":
            found.add("Fleet")
        if "/.github/workflows/" in f"/{lowered}" and name.endswith((".yml", ".yaml")):
            found.add("GitHub Actions")
        if name in {"pyproject.toml", "requirements.txt", "setup.py"}:
            found.add("Python")
        if name == "tsconfig.json":
            found.add("TypeScript")
        if name.endswith(".csproj") or name.endswith(".sln"):
            found.add("C#/.NET")
    return found


def tech_from_manifest(text):
    found = set()
    lowered = text.lower()
    for dep, tech in DEPS.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(dep)}(?![a-z0-9])", lowered):
            found.add(tech)
    return found


def read_text(token, full_name, path, ref):
    quoted = urllib.parse.quote(full_name, safe="/")
    file_path = urllib.parse.quote(path, safe="/")
    branch = urllib.parse.quote(ref, safe="")
    data, _ = fetch(f"{API}/repos/{quoted}/contents/{file_path}?ref={branch}", token)
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return ""
    raw = base64.b64decode(data.get("content") or "")
    if len(raw) > 200_000:
        return ""
    return raw.decode("utf-8", errors="ignore")


def ordered_tech(found):
    return [name for name in TECH_ORDER if name in found]


def theme_for(tech):
    names = set(tech)
    infra = bool(names & INFRA)
    app = bool(names & APP)
    dotnet = "C#/.NET" in names
    if infra and (app or dotnet):
        theme = "automation and services"
    elif infra:
        theme = "infra automation"
    elif dotnet:
        theme = "control software"
    elif app:
        theme = "application services"
    else:
        theme = ""
    return theme if theme in THEMES else ""


def language_shares(byte_counts):
    total = sum(byte_counts.values())
    if total <= 0:
        return []
    ranked = sorted(byte_counts.items(), key=lambda item: (-item[1], item[0]))
    floors = []
    used = 0
    for name, count in ranked[:4]:
        pct = int(count * 100 / total)
        if pct <= 0:
            continue
        floors.append({"name": name, "pct": pct})
        used += pct
    if floors and used < 100 and len(ranked) <= 4:
        floors[0]["pct"] += 100 - used
    return floors


def backfill_counts(token, today):
    start = window_start(today)
    start_at = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_at = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        private_days = contribution_days(token, viewer=True, start=start_at, end=end_at)
        public_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        public_days = contribution_days(public_token, viewer=False, start=start_at, end=end_at)
        if not calendar_covers(private_days, today) or not calendar_covers(public_days, today):
            return None
        return weekly_private_counts(private_days, public_days, today)
    except Exception:
        return None


def calendar_covers(days, today):
    if not days:
        return False
    found = set()
    for day in days:
        try:
            found.add(week_start(date.fromisoformat(day["date"])).isoformat())
        except (TypeError, ValueError):
            continue
    return all(week in found for week in recent_week_starts(today))


def weekly_private_counts(private_days, public_days, today):
    start = window_start(today)
    public = {day["date"]: int(day["contributionCount"]) for day in public_days}
    totals = {}
    for day in private_days:
        day_name = day["date"]
        if day_name > today.isoformat():
            continue
        count = max(0, int(day["contributionCount"]) - public.get(day_name, 0))
        week = week_start(date.fromisoformat(day_name)).isoformat()
        if week < start.isoformat():
            continue
        totals[week] = totals.get(week, 0) + count
    for week in recent_week_starts(today):
        totals.setdefault(week, 0)
    return totals


def contribution_days(token, viewer, start, end):
    if viewer:
        query = """
        query($from: DateTime!, $to: DateTime!) {
          viewer {
            login
            contributionsCollection(from: $from, to: $to) {
              contributionCalendar { weeks { contributionDays { date contributionCount } } }
            }
          }
        }
        """
        variables = {"from": start, "to": end}
    else:
        query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              contributionCalendar { weeks { contributionDays { date contributionCount } } }
            }
          }
        }
        """
        variables = {"login": USER, "from": start, "to": end}
    payload = graphql(query, variables, token)
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    root = (payload.get("data") or {}).get("viewer" if viewer else "user") or {}
    if viewer and root.get("login") != USER:
        return None
    weeks = ((root.get("contributionsCollection") or {}).get("contributionCalendar") or {}).get("weeks") or []
    days = []
    for week in weeks:
        for day in week.get("contributionDays") or []:
            if day.get("date"):
                days.append({"date": day["date"], "contributionCount": int(day.get("contributionCount") or 0)})
    return days


def graphql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "jtalborough-feed",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{API}/graphql", data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def load_history(path):
    if not path.exists():
        return {"snapshots": [], "weeks": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"snapshots": [], "weeks": []}
    if not isinstance(data, dict):
        return {"snapshots": [], "weeks": []}
    snapshots = []
    for snap in data.get("snapshots") or []:
        if not isinstance(snap, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(snap.get("date") or "")):
            continue
        try:
            snapshots.append(normalize_snapshot(snap))
        except (TypeError, ValueError):
            continue
    weeks = []
    for row in data.get("weeks") or []:
        if not isinstance(row, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("week") or "")):
            continue
        raw = row.get("commits")
        if raw is None:
            raw = row.get("contributions")
        try:
            weeks.append({"week": row["week"], "commits": max(0, int(raw or 0))})
        except (TypeError, ValueError):
            continue
    snapshots.sort(key=lambda snap: snap["date"])
    weeks.sort(key=lambda row: row["week"])
    return {"snapshots": snapshots, "weeks": weeks}


def normalize_snapshot(snap):
    tech = [name for name in TECH_ORDER if name in (snap.get("tech") or [])]
    theme = snap.get("theme") if snap.get("theme") in THEMES else ""
    languages = []
    for item in snap.get("languages") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not SAFE_LANGUAGE.fullmatch(name):
            continue
        languages.append({"name": name, "pct": max(0, min(100, int(item.get("pct") or 0)))})
    day = date.fromisoformat(snap["date"])
    week = snap.get("week")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(week or "")):
        week = week_start(day).isoformat()
    return {
        "commits_7d": max(0, int(snap.get("commits_7d") or 0)),
        "commits_30d": max(0, int(snap.get("commits_30d") or 0)),
        "date": snap["date"],
        "languages": languages,
        "repos_7d": max(0, int(snap.get("repos_7d") or 0)),
        "repos_30d": max(0, int(snap.get("repos_30d") or 0)),
        "tech": tech,
        "theme": theme,
        "week": week,
    }


def scrub_snapshot(snapshot, banned):
    banned_lower = {name.lower() for name in banned if name}
    snapshot["languages"] = [
        item for item in snapshot["languages"] if item["name"].lower() not in banned_lower
    ]
    snapshot["tech"] = [name for name in snapshot["tech"] if name.lower() not in banned_lower]
    if snapshot["theme"].lower() in banned_lower:
        snapshot["theme"] = ""
    return snapshot


def upsert_snapshot(history, snapshot):
    history["snapshots"] = [
        item for item in history["snapshots"] if item.get("date") != snapshot["date"]
    ]
    history["snapshots"].append(normalize_snapshot(snapshot))
    history["snapshots"].sort(key=lambda item: item["date"])


def apply_weeks(history, counts):
    by_week = {row["week"]: {"week": row["week"], "commits": row["commits"]} for row in history["weeks"]}
    for week, count in counts.items():
        by_week[week] = {"week": week, "commits": max(0, int(count))}
    history["weeks"] = [by_week[key] for key in sorted(by_week)]


def week_rows(history, today):
    commits = {row["week"]: row["commits"] for row in history["weeks"]}
    return [
        {"week": week, "commits": commits[week] if week in commits else None}
        for week in recent_week_starts(today)
    ]


def series_complete(rows):
    return all(row["commits"] is not None for row in rows)


def latest_snapshot(history):
    if not history["snapshots"]:
        return None
    return history["snapshots"][-1]


def render_private(snapshot, rows, unit="commit"):
    lines = ["### Private work", ""]
    if snapshot:
        lines.append(
            f"Last 7 days: {plural(snapshot['commits_7d'], 'commit')} across {plural(snapshot['repos_7d'], 'active private repo')}. "
            f"Last 30 days: {plural(snapshot['commits_30d'], 'commit')} across {plural(snapshot['repos_30d'], 'active private repo')}."
        )
        language_line = format_languages(snapshot.get("languages") or [])
        if language_line:
            lines.extend(["", language_line])
        if snapshot.get("tech"):
            lines.extend(["", "Tech in the mix: " + ", ".join(snapshot["tech"]) + "."])
        if snapshot.get("theme"):
            lines.extend(["", f"This week: {snapshot['theme']}."])
    if series_complete(rows):
        lines.extend(["", f"![Private {unit}s over the last 12 weeks](data/activity.svg)"])
    week_lines = [line for row in reversed(rows) if (line := week_line(row, unit))]
    if week_lines:
        lines.extend(["", "<details>", "<summary>Past weeks</summary>", ""])
        lines.extend(week_lines)
        lines.extend(["", "</details>"])
    return "\n".join(lines)


def week_line(row, unit="commit"):
    count = row.get("commits")
    if count is None:
        return ""
    return f"- Week of {row['week']}: {plural(count, unit)}."


def plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def format_languages(languages):
    if not languages:
        return ""
    return "Languages: " + " · ".join(f"{item['name']} {item['pct']}%" for item in languages) + "."


def activity_svg(series, unit="commit"):
    width = 720
    height = 128
    left, right, top, bottom = 36, 8, 18, 28
    plot_w = width - left - right
    plot_h = height - top - bottom
    count = max(1, len(series))
    gap = 8
    bar_w = (plot_w - gap * (count - 1)) / count
    peak = max([value for _, value in series] + [1])
    font = "ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"
    desc = " ".join(f"Week of {week}: {plural(value, unit)}." for week, value in series)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" role="img">',
        f"<title>Private {unit}s over the last 12 weeks</title>",
        f"<desc>{desc}</desc>",
        f'<text x="0" y="12" fill="#8b949e" font-family="{font}" font-size="11">{peak}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" stroke="#d0d7de"/>',
    ]
    for index, (week, value) in enumerate(series):
        x = left + index * (bar_w + gap)
        bar_h = 0 if value <= 0 else max(2, round(plot_h * value / peak))
        y = top + plot_h - bar_h
        label = f"{int(week[5:7])}/{int(week[8:10])}"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2" fill="#3fb950">'
            f"<title>Week of {week}: {plural(value, unit)}</title></rect>"
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="#8b949e" '
            f'font-family="{font}" font-size="11">{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def dump_history(history):
    return json.dumps(history, indent=2, sort_keys=True) + "\n"


def leaks(text, banned):
    lowered = text.replace("data/activity.svg", "").replace("data/feed-history.json", "").lower()
    allowed = {name.lower() for name in TECH_ORDER}
    for name in banned:
        if not name or len(name) < 4:
            continue
        if any(mark in name for mark in "/\\._-"):
            if name.lower() in lowered:
                return True
            continue
        if name.lower() in TEMPLATE_WORDS or name.lower() in allowed:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", lowered, re.IGNORECASE):
            return True
    return False


def utc_today():
    return datetime.now(timezone.utc).date()


def week_start(day):
    return day - timedelta(days=day.weekday())


def window_start(today):
    return week_start(today) - timedelta(weeks=WEEKS - 1)


def recent_week_starts(today):
    start = window_start(today)
    return [(start + timedelta(weeks=offset)).isoformat() for offset in range(WEEKS)]


def fetch(url, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jtalborough-feed",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            link = response.headers.get("Link", "")
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in {404, 409}:
            return {}, None
        raise
    return payload, next_link(link)


def next_link(header):
    if not header:
        return None
    for part in header.split(","):
        if 'rel="next"' in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1)
    return None


if __name__ == "__main__":
    main()
