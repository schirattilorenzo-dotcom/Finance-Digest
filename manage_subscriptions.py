"""
User Subscription Manager
--------------------------
Runs every 2 hours via GitHub Actions. Reads unread emails sent to
infolsainews@gmail.com and manages per-user RSS feed subscriptions
based on the email subject line (case-insensitive):

  NEW    -> reads the email body as a description of interests, asks
            Gemini to pick the 40 most relevant feeds from
            rss_feed_directory.txt, and adds the user to user_file.csv.
            If the sender's address is already in user_file.csv, does
            nothing (per spec).
  MODIFY -> re-reads the body and updates the user's feeds + interest
            summary in user_file.csv. Creates the user if not already
            present (not explicitly specified — treated as forgiving
            default; flag if you'd rather it be a no-op instead).
  REMOVE -> deletes the sender's row from user_file.csv.
  TEST   -> runs a digest scoped to that single user's feeds/interests
            and emails it back to them immediately.

Processed emails are marked as read (\\Seen) so they aren't reprocessed
on the next run. Unrecognized subjects are left unread/untouched.
"""
import os
import csv
import json
import socket
import imaplib
import email
from email.header import decode_header
from pathlib import Path

import feedparser
from google import genai

INBOX_ADDRESS = os.environ["EMAIL_ADDRESS"]        # infolsainews@gmail.com
INBOX_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
USER_FILE = Path(__file__).resolve().parent / "user_file.csv"
FEED_DIRECTORY_FILE = Path(__file__).resolve().parent / "rss_feed_directory.txt"
USER_FILE_FIELDS = ["email", "feed_names", "interests_summary"]
RECOGNIZED_SUBJECTS = ("new", "modify", "test", "remove")

socket.setdefaulttimeout(10)  # don't let one slow feed stall a test-digest send


# ---------- Feed directory + user file I/O ----------

def load_feed_directory() -> list[dict]:
    """Parse rss_feed_directory.txt into [{name, link, description}, ...].
    Any line without a real http(s) link (headers, notes, category titles)
    is skipped automatically."""
    feeds = []
    for line in FEED_DIRECTORY_FILE.read_text(encoding="utf-8").splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            continue
        name, link, desc = parts
        if not link.startswith("http"):
            continue
        feeds.append({"name": name, "link": link, "description": desc})
    return feeds


def load_users() -> dict[str, dict]:
    if not USER_FILE.exists():
        return {}
    with USER_FILE.open(newline="", encoding="utf-8") as f:
        return {row["email"].lower(): row for row in csv.DictReader(f)}


def save_users(users: dict[str, dict]) -> None:
    with USER_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=USER_FILE_FIELDS)
        writer.writeheader()
        for row in users.values():
            writer.writerow(row)


# ---------- Gemini: pick feeds + summarize interests ----------

def select_feeds_and_summary(email_body: str, all_feeds: list[dict]) -> tuple[list[str], str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    feed_list_text = "\n".join(f"- {f['name']}: {f['description']}" for f in all_feeds)

    prompt = f"""A user emailed us describing their news interests. Based on their
message, select the 40 feed names from the AVAILABLE FEEDS list below that
best match their interests (topics, geography, stocks, industries,
technologies, scientific fields, or political leanings they mention).

Respond with ONLY valid JSON, no markdown fences, no preamble, in this
exact shape:
{{"feeds": ["Exact Feed Name 1", "Exact Feed Name 2", "... 40 total ..."],
  "summary": "up to 500 characters, plain text, summarizing this user's
  main interests for internal reference"}}

Every string in "feeds" must be copied EXACTLY (character-for-character)
from the AVAILABLE FEEDS list below — never invent a name not listed there.

USER'S MESSAGE:
{email_body}

AVAILABLE FEEDS:
{feed_list_text}
"""
    response = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
    raw = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(raw)

    valid_names = {f["name"] for f in all_feeds}
    chosen = [name for name in data.get("feeds", []) if name in valid_names][:40]
    summary = data.get("summary", "").strip()[:500]
    return chosen, summary


# ---------- Test-digest path (personalized, on-demand) ----------

def fetch_headlines_for_feeds(feeds: list[dict], max_items_per_feed: int = 5) -> str:
    lines = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["link"])
        except Exception as e:
            print(f"Skipped '{feed['name']}' — error parsing feed: {e}")
            continue
        if not parsed.entries:
            continue
        for entry in parsed.entries[:max_items_per_feed]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()[:300]
            link = entry.get("link", "").strip()
            if title and link:
                lines.append(f"- [{feed['name']}] {title}: {summary} (Link: {link})")
    return "\n".join(lines)


def summarize_for_user(headlines_text: str, interests_summary: str) -> str:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""You are writing a personalized daily news briefing email for one reader.

This reader's stated interests: {interests_summary}

Start the email with exactly this line, on its own:
Good Morning

Then identify the 10 to 15 most important stories from the raw headlines
below, favoring stories connected to this reader's stated interests where
relevant, while always including major global developments entirely. Merge
near-duplicate headlines about the same story into one topic.

For each topic, use exactly this structure, in this order:
TITLE: a short, punchy title line in capital letters
SENTIMENT: one word only — Positive, Neutral, or Negative
GEOGRAPHY: one word only -country, continent or geographic area
SOURCE: the exact link (starting with http), copied exactly from the
matching headline below — never invent a link.
Then a 4-5 sentence paragraph explaining what happened and why it matters.

Leave one blank line between topics. Plain text only, no markdown symbols.

RAW HEADLINES:
{headlines_text}
"""
    response = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
    return response.text


def send_test_digest(sender: str, user_row: dict, all_feeds: list[dict]) -> None:
    from daily_finance_digest import send_email  # reuse existing SMTP logic, untouched

    feed_names = {n for n in user_row["feed_names"].split(";") if n}
    selected_feeds = [f for f in all_feeds if f["name"] in feed_names]

    headlines = fetch_headlines_for_feeds(selected_feeds)
    if not headlines:
        print(f"No headlines fetched for {sender}'s feeds — skipping test send.")
        return
    digest = summarize_for_user(headlines, user_row["interests_summary"])
    send_email("Your Test News Digest", digest, sender)


# ---------- Email parsing helpers ----------

def get_email_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                return part.get_payload(decode=True).decode(errors="ignore")
        return ""
    return msg.get_payload(decode=True).decode(errors="ignore")


def get_sender_address(msg: email.message.Message) -> str:
    return email.utils.parseaddr(msg.get("From", ""))[1].lower()


def get_subject(msg: email.message.Message) -> str:
    raw_subject, encoding = decode_header(msg.get("Subject", ""))[0]
    if isinstance(raw_subject, bytes):
        raw_subject = raw_subject.decode(encoding or "utf-8", errors="ignore")
    return raw_subject.strip().lower()


# ---------- Main loop ----------

def process_inbox() -> None:
    all_feeds = load_feed_directory()
    users = load_users()

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(INBOX_ADDRESS, INBOX_APP_PASSWORD)
    imap.select("INBOX")

    status, message_ids = imap.search(None, "UNSEEN")
    if status != "OK":
        print("IMAP search failed.")
        imap.logout()
        return

    for msg_id in message_ids[0].split():
        msg_id_str = msg_id.decode(errors="ignore") if isinstance(msg_id, bytes) else str(msg_id)
        try:
            # Use BODY.PEEK[] so fetching doesn't auto-mark the email as read
            status, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                print(f"Failed to fetch message ID {msg_id_str}")
                continue
                
            msg = email.message_from_bytes(msg_data[0][1])

            subject = get_subject(msg)
            if subject not in RECOGNIZED_SUBJECTS:
                continue  # leave unread — not a command this app understands

            sender = get_sender_address(msg)
            body = get_email_body(msg)
            print(f"Processing '{subject}' from {sender}")

            if subject == "new":
                if sender in users:
                    print(f"{sender} already exists — skipping (per spec).")
                else:
                    feeds, summary = select_feeds_and_summary(body, all_feeds)
                    users[sender] = {"email": sender, "feed_names": ";".join(feeds), "interests_summary": summary}
                    save_users(users)

            elif subject == "modify":
                feeds, summary = select_feeds_and_summary(body, all_feeds)
                users[sender] = {"email": sender, "feed_names": ";".join(feeds), "interests_summary": summary}
                save_users(users)

            elif subject == "remove":
                if sender in users:
                    users.pop(sender, None)
                    save_users(users)

            elif subject == "test":
                if sender not in users:
                    print(f"{sender} not found in user_file.csv — cannot run test digest.")
                else:
                    send_test_digest(sender, users[sender], all_feeds)

            # If execution reaches this point, processing was successful. Mark as read.
            imap.store(msg_id, "+FLAGS", "\\Seen")

        except Exception as e:
            # Catch failures (including Gemini API and network issues) to keep the script running
            print(f"Error processing email ID {msg_id_str}: {e}")
            try:
                # Explicitly revert flag to unread to guarantee it can be reprocessed
                imap.store(msg_id, "-FLAGS", "\\Seen")
            except Exception as flag_err:
                print(f"Failed to reset Seen flag for message {msg_id_str}: {flag_err}")

    imap.logout()


if __name__ == "__main__":
    process_inbox()
