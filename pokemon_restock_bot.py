import json
from datetime import datetime, timedelta, timezone

import requests


NTFY_TOPIC = "kelvin-pokemon-restocks-change-this"
SEARCH_PHRASE = "Pokemon Center Restocks"

BLUESKY_SEARCH_URL = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
)

NTFY_BASE_URL = "https://ntfy.sh"

MAX_POST_AGE_MINUTES = 30
TIMEOUT_SECONDS = 30


def parse_time(timestamp):
    if not timestamp:
        return None

    try:
        return datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except ValueError:
        return None


def create_post_url(post):
    uri = post.get("uri", "")
    author = post.get("author", {})
    handle = author.get("handle", "")

    if not uri or not handle:
        return None

    post_id = uri.rsplit("/", 1)[-1]

    return f"https://bsky.app/profile/{handle}/post/{post_id}"


def get_previous_alerts():
    response = requests.get(
        f"{NTFY_BASE_URL}/{NTFY_TOPIC}/json",
        params={
            "poll": "1",
            "since": "2h",
        },
        timeout=TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    previous_urls = set()

    for line in response.text.splitlines():
        if not line.strip():
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        click_url = message.get("click")

        if click_url:
            previous_urls.add(click_url)

    return previous_urls


def search_bluesky():
    response = requests.get(
        BLUESKY_SEARCH_URL,
        params={
            "q": f'"{SEARCH_PHRASE}"',
            "sort": "latest",
            "limit": 100,
        },
        timeout=TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    posts = response.json().get("posts", [])
    matches = []

    phrase = SEARCH_PHRASE.lower()

    oldest_allowed = (
        datetime.now(timezone.utc)
        - timedelta(minutes=MAX_POST_AGE_MINUTES)
    )

    for post in posts:
        record = post.get("record", {})
        text = record.get("text", "")

        if phrase not in text.lower():
            continue

        created_at = parse_time(record.get("createdAt"))

        if created_at is None:
            created_at = parse_time(post.get("indexedAt"))

        if created_at is None:
            continue

        if created_at < oldest_allowed:
            continue

        post["_created_at"] = created_at
        matches.append(post)

    matches.sort(key=lambda post: post["_created_at"])

    return matches


def send_notification(post, post_url):
    author = post.get("author", {})
    handle = author.get("handle", "unknown")

    record = post.get("record", {})
    text = " ".join(record.get("text", "").split())

    if len(text) > 300:
        text = text[:297] + "..."

    response = requests.post(
        f"{NTFY_BASE_URL}/",
        json={
            "topic": NTFY_TOPIC,
            "title": "Pokemon Center Restock Alert",
            "message": f"@{handle}: {text}",
            "priority": 4,
            "tags": ["shopping_cart"],
            "click": post_url,
        },
        timeout=TIMEOUT_SECONDS,
    )

    response.raise_for_status()


def main():
    print(f'Searching Bluesky for "{SEARCH_PHRASE}"...')

    previous_alerts = get_previous_alerts()
    posts = search_bluesky()

    alerts_sent = 0

    for post in posts:
        post_url = create_post_url(post)

        if not post_url:
            continue

        if post_url in previous_alerts:
            print(f"Already alerted: {post_url}")
            continue

        send_notification(post, post_url)

        previous_alerts.add(post_url)
        alerts_sent += 1

        print(f"Alert sent: {post_url}")

    if alerts_sent == 0:
        print("No new matching posts found.")
    else:
        print(f"Sent {alerts_sent} alert(s).")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as error:
        print(f"Request failed: {error}")
        raise SystemExit(1)