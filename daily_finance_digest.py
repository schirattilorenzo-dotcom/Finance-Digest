"""
Daily Economic & Global Finance Digest
---------------------------------------
1. Pulls recent headlines from ~50 reputable finance/economic RSS feeds
   (major news outlets, market-data sites, and official institutions
   like central banks and regulators).
2. Sends them to Google's Gemini API (free tier) and asks for a
   7-10 topic morning briefing, each with a title, sentiment tag, and
   source link.
3. Emails the result to you via Gmail SMTP.
Runs automatically once a day via the GitHub Actions workflow in
.github/workflows/Daily digest.yml — see that file's comments for setup.
"""
import os
import socket
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import feedparser
from google import genai
 
# Prevent a single slow/unresponsive feed from stalling the whole run.
socket.setdefaulttimeout(10)
 
# ---- 1. Where the news comes from ----
# Each entry is (friendly_source_name, feed_url). If one ever breaks or
# moves (publishers change RSS URLs occasionally), fetch_headlines()
# below just skips it and prints a warning to the Action log — nothing
# crashes. Check the log after your first few runs and prune any feed
# that consistently shows "returned no entries."
RSS_FEEDS = [
    # --- US business & markets ---
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC World Top News", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch Top Stories", "http://feeds.marketwatch.com/marketwatch/topstories/"),
    ("MarketWatch Real-Time Headlines", "http://feeds.marketwatch.com/marketwatch/realtimeheadlines/"),
    ("Investing.com", "https://www.investing.com/rss/news.rss"),
    ("Seeking Alpha Market News", "https://seekingalpha.com/feed.xml"),
    ("Benzinga", "https://feeds.benzinga.com/benzinga"),
    ("MarketBeat", "https://www.marketbeat.com/feed/"),
    ("Business Insider", "https://feeds.businessinsider.com/custom/all"),
    ("Fox Business", "https://moxie.foxbusiness.com/google-publisher/latest.xml"),
    ("Forbes Business", "https://www.forbes.com/business/feed/"),
    ("Fortune", "https://fortune.com/feed/"),
    ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline"),
    ("TheStreet", "https://www.thestreet.com/.rss/full/"),
    ("Investor's Business Daily", "https://www.investors.com/feed/"),
    ("Kiplinger", "https://www.kiplinger.com/feed/all"),
    ("ZeroHedge", "https://feeds.feedburner.com/zerohedge/feed"),
    ("The Hill – Business", "https://thehill.com/business/feed/"),
    ("USA Today – Money", "https://rssfeeds.usatoday.com/usatoday-moneytopstories"),
    ("ABC News – Business", "https://abcnews.go.com/abcnews/moneyheadlines"),
    ("NYTimes – Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Washington Post – Business", "http://feeds.washingtonpost.com/rss/business"),
    ("WSJ – Markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("Politico – Economy", "https://rss.politico.com/economy.xml"),
    ("Financial Times – International", "https://www.ft.com/rss/home/international"),
 
    # --- International press ---
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("The Guardian – Business", "https://www.theguardian.com/business/rss"),
    ("Sky News – Business", "https://feeds.skynews.com/feeds/rss/business.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Deutsche Welle – Business", "https://rss.dw.com/xml/rss-en-bus"),
    ("Euronews – Business", "https://www.euronews.com/rss?level=theme&name=business"),
    ("The Economist – Finance & Economics", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("South China Morning Post – Business", "https://www.scmp.com/rss/92/feed"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ("Moneycontrol (India)", "https://www.moneycontrol.com/rss/business.xml"),
    ("Livemint – Markets (India)", "https://www.livemint.com/rss/markets"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
 
    # --- Commodities, crypto & FX ---
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("OilPrice.com", "https://oilprice.com/rss/main"),
    ("Kitco News", "https://www.kitco.com/rss/KitcoNews.xml"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
 
    # --- Central banks & official institutions ---
    ("US Federal Reserve – Press Releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("US SEC – Press Releases", "https://www.sec.gov/news/pressreleases.rss"),
    ("European Central Bank – Press", "https://www.ecb.europa.eu/rss/press.xml"),
    ("IMF – News", "https://www.imf.org/en/News/RSS"),
    ("World Bank – News", "https://www.worldbank.org/en/news/all.rss"),
    ("Bank of England – News", "https://www.bankofengland.co.uk/rss/news"),
    ("US Bureau of Labor Statistics", "https://www.bls.gov/feed/bls_latest.rss"),
]
 
MAX_ITEMS_PER_FEED = 4     # lower than before since we now have ~50 feeds, not 4
SUMMARY_MAX_CHARS = 300    # keep each snippet short so the prompt stays a reasonable size
 
 
def fetch_headlines() -> str:
    """Pull recent headlines + short summaries + links from each RSS feed."""
    lines = []
    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"Skipped '{source_name}' — error parsing feed: {e}")
            continue
 
        if not feed.entries:
            print(f"Warning: '{source_name}' returned no entries (feed may be down or moved).")
            continue
 
        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()[:SUMMARY_MAX_CHARS]
            link = entry.get("link", "").strip()
            if title and link:
                lines.append(f"- [{source_name}] {title}: {summary} (Link: {link})")
 
    return "\n".join(lines)
 
 
def summarize_with_gemini(headlines_text: str) -> str:
    """Send the day's headlines to Gemini and get back a 7-10 topic digest."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""You are writing a daily economic and global finance briefing email
for one reader named Lorenzo.
 
Start the email with exactly this line, on its own:
Good Morning Lorenzo
 
Then, below that, identify the 7 to 10 most important economic and global
finance stories from the raw headlines provided below. Merge near-duplicate
headlines about the same story into a single topic rather than listing them
separately.
 
For each topic, use exactly this structure, in this order:
TITLE: a short, punchy title line in capital letters
SENTIMENT: one word only — Positive, Neutral, or Negative — reflecting the
likely market or economic impact of the story
SOURCE: the exact link (starting with http) for this story, copied exactly
from the "(Link: ...)" part of the matching headline below. Never invent
or guess a link — only use one that appears verbatim in the raw headlines.
Then write a 3-4 sentence paragraph explaining what happened and why it matters.
 
Leave one blank line between topics. Use plain text only (no markdown
symbols like ** or #). Keep the whole thing skimmable.
 
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
