import json
import os
import urllib.request
from pathlib import Path

USER = "jtalborough"
README = Path(__file__).with_name("README.md")
START = "<!-- FEED:START -->"
END = "<!-- FEED:END -->"
LIMIT = 8
SKIP = {f"{USER}/{USER}"}
API = "https://api.github.com"


def main():
    text = README.read_text()
    if START not in text or END not in text or text.find(START) > text.find(END):
        raise SystemExit("feed markers missing")
    updated = splice(text, collect())
    if updated != text:
        README.write_text(updated)


def collect():
    source = os.environ.get("FEED_SOURCE", "github")
    if source == "github":
        lines = github_activity()
    else:
        raise SystemExit(f"unknown FEED_SOURCE: {source}")
    return "\n".join(lines) if lines else "No recent public activity."


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
    date = event["created_at"][:10]
    name = repo.split("/", 1)[-1]
    if kind == "PushEvent":
        sha = payload.get("head") or ""
        subject = commit_subject(repo, sha) if sha else ""
        link = f"{repo_url(repo)}/commit/{sha}" if sha else repo_url(repo)
        detail = f" — {plain(subject)}" if subject else ""
        return f"- Pushed to [{name}]({link}){detail} — {date}"
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
        return f"- {verb} [{name}#{number}]({url}) — {title} — {date}"
    if kind == "IssuesEvent":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = plain(issue.get("title") or "")
        action = payload.get("action") or "updated"
        verb = action[:1].upper() + action[1:]
        url = issue.get("html_url") or f"{repo_url(repo)}/issues/{number}"
        return f"- {verb} [{name}#{number}]({url}) — {title} — {date}"
    if kind == "ReleaseEvent":
        release = payload.get("release") or {}
        tag = plain(release.get("tag_name") or "release")
        url = release.get("html_url") or repo_url(repo)
        return f"- Released [{name} {tag}]({url}) — {date}"
    if kind == "CreateEvent":
        return f"- Created [{name}]({repo_url(repo)}) — {date}"
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
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jtalborough-feed",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


if __name__ == "__main__":
    main()
