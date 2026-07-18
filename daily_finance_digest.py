"""
Daily Finance Digest
---------------------
Runs on a schedule to generate personalized news updates. Reads user_file.csv,
fetches the relevant RSS feeds from rss_feed_directory.txt, and sends a 
summarized briefing using Gemini 3.5 tailored to each user's interests.
"""
import os
import csv
import socket
import smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import feedparser
from google import genai

INBOX_ADDRESS = os.environ.get("EMAIL_ADDRESS")        # infolsainews@gmail.com
INBOX_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
USER_FILE = Path(__file__).resolve().parent / "user_file.csv"
FEED_DIRECTORY_FILE = Path(__file__).resolve().parent / "rss_feed_directory.txt"

socket.setdefaulttimeout(10)  # Avoid hung sockets during slow feed parsing


# ---------- Email sending utility ----------

def send_email(subject: str, body: str, recipient: str) -> None:
    """Sends a plain text email to the target recipient using Gmail SMTP."""
    if not INBOX_ADDRESS or not INBOX_APP_PASSWORD:
        print("Missing EMAIL_ADDRESS or EMAIL_APP_PASSWORD env variables. Cannot send email.")
        return

    msg = MIMEMultipart()
    msg["From"] = INBOX_ADDRESS
    msg["To"] = recipient
    msg["Subject"] = subject
    
    msg.attach(MIMEText(body, "plain", "utf-8"))
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(INBOX_ADDRESS, INBOX_APP_PASSWORD)
            server.send_message(msg)
        print(f"Sent email successfully to {recipient}")
    except Exception as e:
        print(f"Failed to send email to {recipient}: {e}")


# ---------- Helper I/O functions ----------

def load_feed_directory() -> list[dict]:
    """Parse rss_feed_directory.txt into [{name, link, description}, ...]."""
    if not FEED_DIRECTORY_FILE.exists():
        print(f"Warning: feed directory file not found at {FEED_DIRECTORY_FILE}")
        return []
    
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


def load_users() -> list[dict]:
    """Load users from the CSV file."""
    if not USER_FILE.exists():
        print(f"No user file found at {USER_FILE}. Skipping generation.")
        return []
    with USER_FILE.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- Headline Aggregator ----------

def fetch_headlines_for_user(user_row: dict, all_feeds: list[dict], max_items: int = 300) -> str:
    """Fetch recent entries from the RSS feeds mapped to this user."""
    user_feed_names = {name.strip() for name in user_row.get("feed_names", "").split(";") if name.strip()}
    user_feeds = [f for f in all_feeds if f["name"] in user_feed_names]
    
    if not user_feeds:
        return ""

    lines = []
    for feed in user_feeds:
        try:
            parsed = feedparser.parse(feed["link"])
        except Exception as e:
            print(f"Error parsing feed '{feed['name']}' for {user_row['email']}: {e}")
            continue
        
        if not parsed.entries:
            continue
            
        for entry in parsed.entries[:max_items]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()[:300]
            link = entry.get("link", "").strip()
            if title and link:
                lines.append(f"- [{feed['name']}] {title}: {summary} (Link: {link})")
                
    return "\n".join(lines)


# ---------- Gemini API Summary call ----------

def generate_personalized_digest(headlines_text: str, interests_summary: str) -> str:
    """Instruct Gemini to compile the custom daily news briefing."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    
    prompt = f"""You are writing a personalized news briefing email for one reader.

This reader's stated interests, language and requirements: {interests_summary}

This reader's stated interests, topics to focus on, language and other requirements: {interests_summary}

Start the email with exactly this line, on its own:
Daily News

Then identify from 5 to 25 most important stories from the raw headlines
below, that fit the reader's stated interests. 
Only choose those headlines that are specifically relevant for the reader.
Merge near-duplicate headlines about the same story into one topic.

For each topic, use exactly this structure, in this order:
TITLE: a short, punchy title line in capital letters
DATE: Date when the news has been published
SENTIMENT: one word only — Positive, Neutral, or Negative
GEOGRAPHY: one word only -country, continent or geographic area
SOURCE: the exact link (starting with http), copied exactly from the
matching headline below — never invent a link.
Then a 5-6 sentence paragraph explaining what happened and why it matters in relation to the user's state interest
You can include humoristic comments in 1 or 2 news.

Leave one blank line between topics. Plain text only, no markdown symbols.
RAW HEADLINES:
{headlines_text}
"""
    response = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
    return response.text


# ---------- Main Loop ----------

def main() -> None:
    all_feeds = load_feed_directory()
    users = load_users()
    
    if not all_feeds or not users:
        print("Required data (feeds or users) not available. Exiting.")
        return

    for user in users:
        email_addr = user.get("email", "").strip()
        interests = user.get("interests_summary", "").strip()
        
        if not email_addr:
            continue
            
        print(f"Compiling digest for: {email_addr}")
        
        # 1. Fetch raw headlines for this user's feeds
        headlines_text = fetch_headlines_for_user(user, all_feeds)
        if not headlines_text:
            print(f"No recent headlines found for {email_addr}'s feeds. Skipping.")
            continue
            
        # 2. Call Gemini 3.5 to filter, format, and summarize
        try:
            digest_body = generate_personalized_digest(headlines_text, interests)
        except Exception as e:
            print(f"API generation failed for {email_addr}: {e}")
            continue
            
        # 3. Send email immediately
        send_email(
            subject="DailyNews", 
            body=digest_body, 
            recipient=email_addr
        )


if __name__ == "__main__":
    main()
