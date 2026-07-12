"""
Daily Economic & Global Finance Digest
---------------------------------------
1. Pulls recent headlines from a handful of finance RSS feeds.
2. Sends them to Google's Gemini API (free tier) and asks for a
   7-10 topic summary, written like a morning briefing.
3. Emails the result to you via Gmail SMTP.

Runs automatically once a day via the GitHub Actions workflow in
.github/workflows/daily-digest.yml — see that file's comments for setup.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import feedparser
from google import genai

# ---- 1. Where the news comes from ----
# Add or remove feeds here. If one ever breaks (sites change URLs
# occasionally), just delete or swap that line.
RSS_FEEDS = [
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",     # CNBC Finance
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",     # CNBC Economy
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",    # CNBC World Top News
    "https://finance.yahoo.com/news/rssindex",                  # Yahoo Finance
]

MAX_ITEMS_PER_FEED = 15  # keep the prompt a reasonable size


def fetch_headlines() -> str:
    """Pull recent headlines + short summaries from each RSS feed."""
    lines = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            if title:
                lines.append(f"- {title}: {summary}")
    return "\n".join(lines)


def summarize_with_gemini(headlines_text: str) -> str:
    """Send the day's headlines to Gemini and get back a 7-10 topic digest."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""You are writing a daily economic and global finance briefing email
for one reader.

Below are raw headlines and snippets pulled from several finance news feeds
today. Identify the 7 to 10 most important economic and global finance
stories. Merge near-duplicate headlines about the same story into a single
topic rather than listing them separately.

For each topic, write:
- A short, punchy title line in capital letters
- A 3-4 sentence paragraph explaining what happened and why it matters

Use plain text only (no markdown symbols like ** or #). Keep the whole
thing skimmable.

RAW HEADLINES:
{headlines_text}
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
    )
    return response.text


def send_email(subject: str, body: str, to_address: str) -> None:
    """Send the digest via Gmail SMTP using an app password."""
    from_address = os.environ["EMAIL_ADDRESS"]
    app_password = os.environ["EMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(from_address, app_password)
        server.sendmail(from_address, to_address, msg.as_string())


def main() -> None:
    headlines = fetch_headlines()
    if not headlines:
        raise RuntimeError("No headlines were fetched — check the RSS feed URLs.")

    digest = summarize_with_gemini(headlines)

    today = datetime.now().strftime("%B %d, %Y")
    subject = f"Daily Economic & Global Finance Briefing - {today}"

    send_email(subject, digest, os.environ["RECIPIENT_EMAIL"])
    print("Digest sent successfully.")


if __name__ == "__main__":
    main()
