import asyncio
import json
import time
from urllib.parse import urlencode

import requests
import websockets


# ============================================================
# CHANGE THIS TO THE TOPIC YOU SUBSCRIBED TO IN THE NTFY APP
# ============================================================

NTFY_TOPIC = "kelvin-pokemon-restocks-12345"


# ============================================================
# BOT SETTINGS
# ============================================================

SEARCH_PHRASE = "Pokemon Center Restocks"

# Each GitHub run looks back ten minutes.
# Since GitHub runs every five minutes, this gives us overlap.
LOOKBACK_MINUTES = 10

# Stop the script if Bluesky takes too long to replay the events.
MAX_RUN_SECONDS = 240

JETSTREAM_BASE_URL = (
    "wss://jetstream2.us-east.bsky.network/subscribe"
)

NTFY_BASE_URL = "https://ntfy.sh"

REQUEST_TIMEOUT_SECONDS = 30


def get_previous_alerts():
    """
    Read recent notifications from ntfy so overlapping GitHub
    runs do not alert for the same Bluesky post twice.
    """
    response = requests.get(
        f"{NTFY_BASE_URL}/{NTFY_TOPIC}/json",
        params={
            "poll": "1",
            "since": "2h",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
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

        if message.get("event") != "message":
            continue

        click_url = message.get("click")

        if isinstance(click_url, str):
            previous_urls.add(click_url)

    return previous_urls


def create_post_url(did, post_id):
    """
    Build a clickable Bluesky URL.

    Bluesky accepts the account DID in the profile portion
    of the URL, so we do not need to look up the username.
    """
    return f"https://bsky.app/profile/{did}/post/{post_id}"


def send_notification(post_text, post_url):
    """
    Send a push notification through ntfy.
    """
    clean_text = " ".join(post_text.split())

    if len(clean_text) > 350:
        clean_text = clean_text[:347] + "..."

    response = requests.post(
        f"{NTFY_BASE_URL}/",
        json={
            "topic": NTFY_TOPIC,
            "title": "Pokemon Center Restock Alert",
            "message": clean_text,
            "priority": 4,
            "tags": ["shopping_cart"],
            "click": post_url,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()


async def monitor_bluesky():
    """
    Replay the previous few minutes of Bluesky posts through
    Jetstream, find matching posts, send alerts, and exit.
    """
    now_microseconds = int(time.time() * 1_000_000)

    lookback_microseconds = (
        LOOKBACK_MINUTES
        * 60
        * 1_000_000
    )

    cursor = now_microseconds - lookback_microseconds

    query = urlencode(
        {
            "wantedCollections": "app.bsky.feed.post",
            "cursor": cursor,
        }
    )

    websocket_url = f"{JETSTREAM_BASE_URL}?{query}"

    print(
        f'Watching Bluesky for "{SEARCH_PHRASE}" '
        f"over the previous {LOOKBACK_MINUTES} minutes..."
    )

    previous_alerts = get_previous_alerts()

    phrase = SEARCH_PHRASE.casefold()

    events_checked = 0
    alerts_sent = 0

    async with websockets.connect(
        websocket_url,
        open_timeout=30,
        ping_interval=20,
        ping_timeout=20,
        max_size=2_000_000,
    ) as websocket:

        while True:
            raw_message = await websocket.recv()

            try:
                event = json.loads(raw_message)
            except json.JSONDecodeError:
                continue

            event_time = int(event.get("time_us", 0))
            events_checked += 1

            if events_checked % 50_000 == 0:
                print(
                    f"Checked {events_checked:,} Bluesky events..."
                )

            if event.get("kind") == "commit":
                commit = event.get("commit") or {}

                operation = commit.get("operation")
                collection = commit.get("collection")

                if (
                    operation == "create"
                    and collection == "app.bsky.feed.post"
                ):
                    record = commit.get("record") or {}
                    post_text = str(record.get("text", ""))

                    if phrase in post_text.casefold():
                        did = event.get("did")
                        post_id = commit.get("rkey")

                        if did and post_id:
                            post_url = create_post_url(
                                did=did,
                                post_id=post_id,
                            )

                            if post_url in previous_alerts:
                                print(
                                    f"Already alerted: {post_url}"
                                )
                            else:
                                send_notification(
                                    post_text=post_text,
                                    post_url=post_url,
                                )

                                previous_alerts.add(post_url)
                                alerts_sent += 1

                                print(
                                    f"Alert sent: {post_url}"
                                )

            # Once Jetstream reaches events created after this
            # script started, the replay has caught up.
            if event_time >= now_microseconds:
                break

    print(f"Checked {events_checked:,} events.")

    if alerts_sent == 0:
        print("No new matching posts found.")
    else:
        print(f"Sent {alerts_sent} alert(s).")


async def main():
    try:
        await asyncio.wait_for(
            monitor_bluesky(),
            timeout=MAX_RUN_SECONDS,
        )

    except asyncio.TimeoutError:
        print(
            "The Bluesky check reached its time limit. "
            "The next GitHub run will try again."
        )

    except requests.RequestException as error:
        print(f"ntfy request failed: {error}")
        raise SystemExit(1)

    except websockets.WebSocketException as error:
        print(f"Bluesky connection failed: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nStopped.")