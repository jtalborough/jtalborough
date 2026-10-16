import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USER = "jtalborough"
README = Path(__file__).with_name("README.md")
DATA = Path(__file__).parent / "data"
START = "<!-- FEED:START -->"
END = "<!-- FEED:END -->"
SKIP = {f"{USER}/{USER}"}
API = "https://api.github.com"
COMMIT_CAP = 2000
TECH_CAP = 10
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
    "the",
}


def main():
    text = README.read_text()
    if START not in text or END not in text or text.find(START) > text.find(END):
        raise SystemExit("feed markers missing")
    updated = splice(text, collect())
    if updated != text:
        README.write_text(updated)
    retire_chart()


def collect():
    source = os.environ.get("FEED_SOURCE", "github")
    if source != "github":
        raise SystemExit(f"unknown FEED_SOURCE: {source}")
    public = github_activity()
    private_md = private_output()
    body = "\n".join(public) if public else "No recent public activity."
    if private_md:
        body = f"{body}\n\n{private_md.strip()}"
    return body


def splice(text, body):
    pre, rest = text.split(START, 1)
    _, post = rest.split(END, 1)
    return f"{pre}{START}\n{body.strip()}\n{END}{post}"


def retire_chart():
    for name in ("activity.svg", "feed-history.json"):
        path = DATA / name
        if path.exists():
            path.unlink()
    if DATA.is_dir() and not any(DATA.iterdir()):
        DATA.rmdir()


def github_activity():
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chosen = {}
    for event in public_events(cutoff):
        keep(chosen, event, replace=True)
    for event in search_events(cutoff):
        keep(chosen, event, replace=False)
    for event in push_events(cutoff):
        keep(chosen, event, replace=True)
    ordered = sorted(chosen.values(), key=lambda event: event.get("created_at") or "", reverse=True)
    return [line for event in ordered if (line := format_event(event))]


def keep(chosen, event, replace):
    key = event_key(event)
    if key is None:
        return
    current = chosen.get(key)
    if current is None:
        chosen[key] = event
        return
    if replace and (event.get("created_at") or "") > (current.get("created_at") or ""):
        chosen[key] = event


def public_events(cutoff):
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{API}/users/{USER}/events/public?per_page=100"
    found = []
    while url and len(found) < 300:
        page, url = fetch(url, public_token())
        if not isinstance(page, list):
            raise SystemExit("unexpected events payload")
        if not page:
            break
        for event in page:
            created = event.get("created_at") or ""
            if created < stamp:
                return found
            found.append(event)
    return found


def search_events(cutoff):
    day = cutoff.strftime("%Y-%m-%d")
    query = urllib.parse.quote(f"author:{USER} is:public updated:>={day}")
    url = f"{API}/search/issues?q={query}&per_page=50&sort=updated"
    found = []
    try:
        while url and len(found) < 100:
            data, url = fetch(url, public_token())
            if not isinstance(data, dict):
                break
            for item in data.get("items") or []:
                event = issue_event(item, cutoff)
                if event:
                    found.append(event)
    except Exception:
        return found
    return found


def issue_event(item, cutoff):
    repo_url = item.get("repository_url") or ""
    marker = "/repos/"
    if marker not in repo_url or not item.get("number"):
        return None
    full = repo_url.split(marker, 1)[1]
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = item.get("state") or ""
    created = item.get("created_at") or ""
    updated = item.get("updated_at") or ""
    closed = item.get("closed_at") or ""
    if state == "closed" and closed >= stamp:
        action, when = "closed", closed
    elif created >= stamp:
        action, when = "opened", created
    elif updated >= stamp:
        action, when = "updated", updated
    else:
        return None
    if item.get("pull_request"):
        return {
            "type": "PullRequestEvent",
            "created_at": when,
            "repo": {"name": full},
            "payload": {"action": action, "number": item["number"]},
        }
    return {
        "type": "IssuesEvent",
        "created_at": when,
        "repo": {"name": full},
        "payload": {
            "action": action,
            "issue": {
                "number": item["number"],
                "title": item.get("title") or "",
                "html_url": item.get("html_url") or "",
            },
        },
    }


def push_events(cutoff):
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    token = public_token()
    events = []
    try:
        repos = list_public_repos(token)
    except Exception:
        return events
    for repo in repos:
        full = repo.get("full_name") or ""
        if not full or full in SKIP:
            continue
        try:
            commit = newest_commit(token, full, stamp)
        except Exception:
            continue
        if not commit:
            continue
        sha = commit.get("sha") or ""
        authored = ((commit.get("commit") or {}).get("author") or {}).get("date") or ""
        if not sha or authored < stamp:
            continue
        events.append(
            {
                "type": "PushEvent",
                "created_at": authored,
                "repo": {"name": full},
                "payload": {"head": sha},
            }
        )
    return events


def newest_commit(token, full_name, since):
    quoted = urllib.parse.quote(full_name, safe="/")
    stamp = urllib.parse.quote(since, safe="")
    url = f"{API}/repos/{quoted}/commits?since={stamp}&per_page=100"
    pages = 0
    while url and pages < 5:
        page, url = fetch(url, token)
        pages += 1
        if not isinstance(page, list) or not page:
            return None
        for commit in page:
            if (commit.get("author") or {}).get("login") == USER:
                return commit
    return None


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
    data, _ = fetch(url, public_token())
    return data


def public_token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def private_output():
    token = os.environ.get("PRIVATE_FEED_TOKEN", "").strip()
    if not token:
        print("private section skipped")
        return ""
    try:
        return build_private(token)
    except Exception:
        print("private section skipped")
        return ""


def build_private(token):
    commits, repos, activity_banned = private_commits(token)
    languages, tech_counts, stack_banned = stack_signals(public_token(), token)
    banned = activity_banned | stack_banned
    shares = scrub_languages(language_shares(languages), banned)
    tech = scrub_names(top_tech(tech_counts), banned)
    if commits is None and not shares and not tech:
        raise RuntimeError("private")
    markdown = render_private(commits, repos, shares, tech)
    if leaks(markdown, banned):
        raise RuntimeError("sanitizer")
    return markdown


def private_commits(token):
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = repos = counted = failed = 0
    banned = set()
    for repo in list_private_repos(token):
        full_name = repo.get("full_name") or ""
        name = repo.get("name") or ""
        owner = ((repo.get("owner") or {}).get("login")) or ""
        if full_name:
            banned.add(full_name)
        if name:
            banned.add(name)
        if owner and owner != USER:
            banned.add(owner)
        try:
            got = count_authored(token, full_name, since)
        except Exception:
            failed += 1
            continue
        counted += 1
        commits += got
        if got:
            repos += 1
    if counted == 0 and failed:
        return None, None, banned
    return commits, repos, banned


def count_authored(token, full_name, since):
    quoted = urllib.parse.quote(full_name, safe="/")
    stamp = urllib.parse.quote(since, safe="")
    url = f"{API}/repos/{quoted}/commits?author={USER}&since={stamp}&per_page=100"
    total = 0
    while url and total < COMMIT_CAP:
        page, url = fetch(url, token)
        if not isinstance(page, list) or not page:
            break
        total += len(page)
        del page
    return min(total, COMMIT_CAP)


def stack_signals(public, private):
    languages = {}
    tech_counts = {}
    banned = set()
    groups = []
    try:
        groups.append((list_public_repos(public), public, False))
    except Exception:
        pass
    if private:
        try:
            groups.append((list_private_repos(private), private, True))
        except Exception:
            pass
    seen = set()
    for repos, token, secret in groups:
        for repo in repos:
            full_name = repo.get("full_name") or ""
            if not full_name or full_name in seen:
                continue
            seen.add(full_name)
            if secret:
                name = repo.get("name") or ""
                owner = ((repo.get("owner") or {}).get("login")) or ""
                banned.add(full_name)
                if name:
                    banned.add(name)
                if owner and owner != USER:
                    banned.add(owner)
            try:
                found, paths = repo_tech(token, repo)
                counts = repo_languages(token, full_name)
            except Exception:
                continue
            for language, count in counts.items():
                languages[language] = languages.get(language, 0) + count
            if counts.get("Python"):
                found.add("Python")
            if counts.get("TypeScript"):
                found.add("TypeScript")
            if counts.get("C#"):
                found.add("C#/.NET")
            for name in found:
                tech_counts[name] = tech_counts.get(name, 0) + 1
            if secret:
                banned.update(paths)
            del paths
    return languages, tech_counts, banned


def list_public_repos(token):
    return list_repos(f"{API}/users/{USER}/repos?type=owner&per_page=100", token, private=False)


def list_private_repos(token):
    url = (
        f"{API}/user/repos?visibility=private&per_page=100"
        "&affiliation=owner,collaborator,organization_member"
    )
    return list_repos(url, token, private=True)


def list_repos(url, token, private):
    repos = []
    while url and len(repos) < 100:
        page, url = fetch(url, token)
        if not isinstance(page, list):
            break
        for repo in page:
            if repo.get("fork"):
                continue
            if private and not repo.get("private"):
                continue
            repos.append(repo)
    return repos


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
        if (
            name in {"kustomization.yml", "kustomization.yaml", "chart.yaml"}
            or "/k8s/" in f"/{lowered}"
            or "/kubernetes/" in f"/{lowered}"
        ):
            found.add("Kubernetes")
        if name == "chart.yaml":
            found.add("Helm")
        if name == "dockerfile" or name.startswith("dockerfile.") or name in {
            "docker-compose.yml",
            "docker-compose.yaml",
        }:
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


def language_shares(byte_counts):
    total = sum(byte_counts.values())
    if total <= 0:
        return []
    ranked = sorted(byte_counts.items(), key=lambda item: (-item[1], item[0]))
    shares = []
    for name, count in ranked:
        pct = int(count * 100 / total)
        if pct <= 0:
            continue
        shares.append({"name": name, "pct": pct})
    return shares


def top_tech(counts):
    order = {name: index for index, name in enumerate(TECH_ORDER)}
    ranked = sorted(
        (name for name in counts if name in order),
        key=lambda name: (-counts[name], order[name]),
    )
    return ranked[:TECH_CAP]


def scrub_languages(languages, banned):
    banned_lower = {name.lower() for name in banned if name}
    return [item for item in languages if item["name"].lower() not in banned_lower]


def scrub_names(names, banned):
    banned_lower = {name.lower() for name in banned if name}
    return [name for name in names if name.lower() not in banned_lower]


def render_private(commits, repos, languages, tech):
    lines = ["### Private work", ""]
    if commits is not None:
        lines.append(
            f"Last 30 days: {plural(commits, 'commit')} across {plural(repos, 'active private repo')}."
        )
    language_line = format_languages(languages)
    if language_line:
        lines.extend(["", language_line])
    if tech:
        lines.extend(["", "Tech in the mix: " + ", ".join(tech) + "."])
    if len(lines) == 2:
        return ""
    return "\n".join(lines)


def plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def format_languages(languages):
    if not languages:
        return ""
    return "Languages: " + " · ".join(f"{item['name']} {item['pct']}%" for item in languages) + "."


def leaks(text, banned):
    lowered = text.lower()
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
