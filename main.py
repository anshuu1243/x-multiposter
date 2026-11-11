import os
import io
import json
import random
import string
import asyncio
import tempfile
import aiofiles
import httpx
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageFont
from twikit import Client

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)

CARD_IMAGES_DIR = BASE_DIR / "card_images"
CARD_IMAGES_DIR.mkdir(exist_ok=True)

ACCOUNTS_FILE = BASE_DIR / "accounts.json"
CARDS_FILE = BASE_DIR / "cards.json"
CLICKS_FILE = BASE_DIR / "clicks.json"
SCHEDULE_FILE = BASE_DIR / "scheduled_posts.json"
AUTOPILOT_FILE = BASE_DIR / "autopilot.json"
AUTOPILOT_LOGS_FILE = BASE_DIR / "autopilot_logs.json"

# Secret PIN set to 1801
DEFAULT_PIN = os.getenv("DASHBOARD_PIN", "1801")

app = FastAPI(title="Mr-KING Multi-Poster & Viral Suite")

# ----------------- Helper Functions & Storage ----------------- #

def load_json_file(file_path: Path, default_val: Any) -> Any:
    if not file_path.exists():
        return default_val
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_val

def save_json_file(file_path: Path, data: Any):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving {file_path}: {e}")

def get_saved_accounts() -> List[dict]:
    return load_json_file(ACCOUNTS_FILE, [])

def save_accounts(accounts: List[dict]):
    save_json_file(ACCOUNTS_FILE, accounts)

def get_saved_cards() -> List[dict]:
    return load_json_file(CARDS_FILE, [])

def save_cards(cards: List[dict]):
    save_json_file(CARDS_FILE, cards)

def get_clicks_data() -> dict:
    return load_json_file(CLICKS_FILE, {
        "total_clicks": 0,
        "by_account": {},
        "by_card": {},
        "recent": []
    })

def record_click(card_id: str, account: Optional[str], ip: str = "", user_agent: str = ""):
    data = get_clicks_data()
    data["total_clicks"] = data.get("total_clicks", 0) + 1
    
    # By account breakdown
    clean_acc = account.strip().lstrip("@") if account else "Direct / Unassigned"
    data["by_account"][clean_acc] = data["by_account"].get(clean_acc, 0) + 1
    
    # By card breakdown
    data["by_card"][card_id] = data["by_card"].get(card_id, 0) + 1
    
    # Recent history log (keep last 100)
    history = data.get("recent", [])
    history.insert(0, {
        "card_id": card_id,
        "account": clean_acc,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_agent": user_agent[:60] if user_agent else "Browser"
    })
    data["recent"] = history[:100]
    save_json_file(CLICKS_FILE, data)

def get_scheduled_posts() -> List[dict]:
    return load_json_file(SCHEDULE_FILE, [])

def save_scheduled_posts(posts: List[dict]):
    save_json_file(SCHEDULE_FILE, posts)

# ----------------- Auto-Pilot Engine Storage & Helpers ----------------- #

DEFAULT_AUTOPILOT = {
    "enabled": False,
    "blog_url": "https://kingtrendz007.blogspot.com",
    "blogger_email": "itsanshu.unofficial.king1801@blogger.com",
    "sender_email": "",
    "sender_password": "",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "interval_minutes": 120,
    "vip_hijacker_enabled": True,
    "vip_targets": ["elonmusk", "MrBeast", "FabrizioRomano", "BBCBreaking", "narendramodi", "espncricket"],
    "top_reply_pinner": True,
    "last_run": "",
    "last_post_url": ""
}

def get_autopilot_config() -> dict:
    cfg = load_json_file(AUTOPILOT_FILE, dict(DEFAULT_AUTOPILOT))
    # Ensure missing keys
    for k, v in DEFAULT_AUTOPILOT.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def save_autopilot_config(cfg: dict):
    save_json_file(AUTOPILOT_FILE, cfg)

def get_autopilot_logs() -> List[dict]:
    return load_json_file(AUTOPILOT_LOGS_FILE, [])

def add_autopilot_log(message: str, log_type: str = "info"):
    logs = get_autopilot_logs()
    entry = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "message": message,
        "type": log_type
    }
    logs.insert(0, entry)
    save_json_file(AUTOPILOT_LOGS_FILE, logs[:60])

VIRAL_STORIES_POOL = [
    {
        "title": "Model Forgot Live Camera Was Still Recording: Millions Shocked By What Happened! 😱🔞",
        "category": "Viral Leaks",
        "duration": "03:42",
        "image_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=1200&q=80",
        "story": "A late-night live stream has created complete chaos across Twitter and Reddit today. Thousands of viewers who were tuned in captured the exact moment the stream was left running unattended. The clip has sparked intense debate and curiosity online. Watch the full viral stream recap and unedited breakdown below."
    },
    {
        "title": "Private Hotel Footage Leaked Online: Fans In Absolute Disbelief Over The Ending! 🤫🔥",
        "category": "Celebrity Drama",
        "duration": "04:15",
        "image_url": "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=1200&q=80",
        "story": "Surveillance and sneak-peek clips from a high-profile private party have unexpectedly surfaced on social media. Fans and internet sleuths are rushing to analyze the never-before-seen footage before it gets taken down. See the full viral sequence and timeline breakdown below."
    },
    {
        "title": "Behind The Scenes Photoshoot Clip Leaked: Nobody Expected This Turn of Events! 💋👀",
        "category": "Glamour & Viral",
        "duration": "02:58",
        "image_url": "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=1200&q=80",
        "story": "An exclusive behind-the-scenes recording from a luxury beach photoshoot has caught fire on the internet. The raw, unfiltered video shows unexpected moments that were never meant to be released publicly. Catch the full exclusive footage and details below."
    },
    {
        "title": "Secret Late Night Confession: Live Mic Left On After The Show Ended! 🎬🔥",
        "category": "Trending Romance",
        "duration": "05:12",
        "image_url": "https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=1200&q=80",
        "story": "A shocking audio and video leak from a popular dating show has sent shockwaves through social media. After the cameras officially stopped rolling, the microphones remained active, capturing an intimate and unexpected conversation. Listen to the full recording and breakdown below."
    },
    {
        "title": "Viral Creator Apologizes For Accidental Live Stream Slip-Up: Watch What Happened! 📱🔞",
        "category": "Social Media Leak",
        "duration": "03:25",
        "image_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=1200&q=80",
        "story": "One of the most talked-about social media moments this month occurred when a well-known creator accidentally showed more than intended during a live Q&A session. While the creator issued a public statement, mirror links of the viral footage continue to trend across platforms worldwide."
    },
    {
        "title": "Crazy Private Villa Footage Surfaces: The Viral Clip Breaking The Entire Internet! 💥🤫",
        "category": "Exclusive Scoop",
        "duration": "04:40",
        "image_url": "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=1200&q=80",
        "story": "Sensational footage from a VIP private gathering has leaked into public forums, generating millions of views in just a few hours. Viewers are scouring social channels for the full unedited version. Here is everything you need to know along with the complete viral clip overview."
    },
    {
        "title": "Exclusive Uncut Clip Goes Viral: Millions Searching For What Happened At 02:15! 👀🔥",
        "category": "Mystery & Thrill",
        "duration": "03:10",
        "image_url": "https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=1200&q=80",
        "story": "A captivating and suspenseful video clip has captured the imagination of users globally. At precisely the 2-minute mark, something completely unexpected occurs, sending viewers into a frenzy in the comments section. Watch the full clip and see the reaction for yourself."
    }
]

def generate_viral_article() -> dict:
    story = random.choice(VIRAL_STORIES_POOL)
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 720px; margin: auto; line-height: 1.7; color: #1e293b; background: #ffffff; padding: 15px; border-radius: 16px;">
        
        <!-- Category & Age Badge -->
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
            <span style="background: #fee2e2; color: #dc2626; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px;">🔥 TRENDING VIRAL • 18+ ADVISORY</span>
            <span style="color: #64748b; font-size: 12px;">⏱️ Duration: <strong>{story['duration']}</strong></span>
        </div>

        <!-- Article Headline -->
        <h1 style="color: #0f172a; font-size: 22px; line-height: 1.35; font-weight: 900; margin: 0 0 16px 0;">{story['title']}</h1>

        <!-- High-Impact Faux Video Player -->
        <div style="position: relative; border-radius: 14px; overflow: hidden; background: #000; box-shadow: 0 10px 25px rgba(0,0,0,0.3); margin-bottom: 20px; text-align: center;">
            <img src="{story['image_url']}" alt="{story['title']}" style="width: 100%; height: auto; display: block; opacity: 0.88; filter: brightness(0.92);" />
            
            <!-- Center Play Button with Soft Red/White Glow -->
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 72px; height: 72px; background: rgba(220, 38, 38, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 0 30px rgba(220, 38, 38, 0.8); cursor: pointer; border: 3px solid #ffffff;">
                <span style="color: #ffffff; font-size: 28px; margin-left: 4px; line-height: 1;">▶</span>
            </div>
            
            <!-- Video Duration Pill -->
            <div style="position: absolute; bottom: 12px; right: 12px; background: rgba(0,0,0,0.85); color: #fff; font-size: 11px; font-weight: bold; padding: 4px 8px; border-radius: 6px; font-family: monospace;">
                ▶ {story['duration']} HD
            </div>

            <!-- Live Viewers Pill -->
            <div style="position: absolute; top: 12px; left: 12px; background: rgba(0,0,0,0.75); color: #22c55e; font-size: 11px; font-weight: bold; padding: 3px 8px; border-radius: 6px;">
                ● 48.2K Watching Now
            </div>
        </div>

        <!-- Story Summary Hook -->
        <div style="background: #f8fafc; border-left: 4px solid #ef4444; padding: 16px 18px; border-radius: 8px; margin-bottom: 20px;">
            <p style="margin: 0; font-size: 15px; color: #334155; line-height: 1.6;">
                <strong>Exclusive Report:</strong> {story['story']}
            </p>
        </div>

        <!-- Call-To-Action Button (Adsterra Popunder Trigger) -->
        <div style="text-align: center; margin: 25px 0;">
            <div style="display: inline-block; background: linear-gradient(135deg, #ef4444, #b91c1c); color: #ffffff; font-weight: 800; font-size: 16px; padding: 14px 28px; border-radius: 30px; box-shadow: 0 8px 20px rgba(239, 68, 68, 0.4); cursor: pointer;">
                ▶ Click Here To Watch Full Uncut Video (HD)
            </div>
            <p style="font-size: 11px; color: #94a3b8; margin-top: 8px;">Tap anywhere above to start the player stream.</p>
        </div>

        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0;" />
        <p style="color: #64748b; font-size: 12px; text-align: center;">Verified Media Archive • Published Today</p>
    </div>
    """
    return {
        "title": story["title"],
        "category": story["category"],
        "duration": story["duration"],
        "image_url": story["image_url"],
        "content_html": html_content
    }

def send_blogger_email(to_email: str, subject: str, html_content: str, sender_email: str, sender_password: str, smtp_server: str = "smtp.gmail.com", smtp_port: int = 587) -> bool:
    if not sender_email or not sender_password:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = to_email
        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)
        
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
        server.ehlo()
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email to Blogger: {e}")
        return False

def get_index_html() -> str:
    possible_paths = [
        BASE_DIR / "templates" / "index.html",
        BASE_DIR / "index.html"
    ]
    for p in possible_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return "<h1>index.html nahi mila! Kripya GitHub par index.html file upload karein.</h1>"

def split_text_into_chunks(text: str, max_len: int = 265) -> List[str]:
    text = text.strip()
    if len(text) <= 280:
        return [text]

    words = text.split(" ")
    chunks = []
    current_chunk = []

    for word in words:
        test_chunk = " ".join(current_chunk + [word])
        if len(test_chunk) > max_len:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
            else:
                chunks.append(word[:max_len])
                current_chunk = [word[max_len:]]
        else:
            current_chunk.append(word)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    total = len(chunks)
    return [f"{chunk} ({i+1}/{total})" for i, chunk in enumerate(chunks)]

# ----------------- Dynamic Video Overlay Image Generator ----------------- #

def generate_video_overlay_image(base_img_path: Optional[Path], play_button: bool, duration: str) -> io.BytesIO:
    # 1. Load or create base image (1200x675 standard Twitter card ratio)
    width, height = 1200, 675
    if base_img_path and base_img_path.exists():
        try:
            im = Image.open(base_img_path).convert("RGBA")
            im = im.resize((width, height), Image.Resampling.LANCZOS)
        except Exception:
            im = Image.new("RGBA", (width, height), (15, 23, 42, 255))
    else:
        # Luxury dark gradient placeholder
        im = Image.new("RGBA", (width, height), (10, 15, 25, 255))
        draw_temp = ImageDraw.Draw(im)
        for y in range(height):
            ratio = y / height
            r = int(12 + ratio * 20)
            g = int(18 + ratio * 35)
            b = int(35 + ratio * 70)
            draw_temp.line([(0, y), (width, y)], fill=(r, g, b, 255))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 2. Draw Play Button in exact center
    if play_button:
        cx, cy = width // 2, height // 2
        radius = 70
        
        # Outer soft glow circle
        draw.ellipse([cx - radius - 6, cy - radius - 6, cx + radius + 6, cy + radius + 6], fill=(0, 0, 0, 80))
        # Main dark translucent circle
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(15, 20, 30, 205), outline=(255, 255, 255, 120), width=3)
        
        # Crisp white play triangle pointing right
        tri_size = 32
        p1 = (cx - tri_size // 2 + 5, cy - tri_size)
        p2 = (cx - tri_size // 2 + 5, cy + tri_size)
        p3 = (cx + tri_size + 4, cy)
        draw.polygon([p1, p2, p3], fill=(255, 255, 255, 245))

    # 3. Draw Video Duration badge in bottom-right corner
    clean_duration = duration.strip()
    if clean_duration:
        badge_text = f"▶ {clean_duration}" if not clean_duration.startswith("▶") else clean_duration
        pad_x, pad_y = 18, 10
        # Font calculation
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        
        # Approximate size
        text_w = len(badge_text) * 12
        text_h = 22
        
        rx2 = width - 35
        ry2 = height - 35
        rx1 = rx2 - text_w - (pad_x * 2)
        ry1 = ry2 - text_h - (pad_y * 2)

        # Draw rounded badge
        draw.rounded_rectangle([rx1, ry1, rx2, ry2], radius=12, fill=(0, 0, 0, 210), outline=(255, 255, 255, 50), width=1)
        draw.text((rx1 + pad_x, ry1 + pad_y - 2), badge_text, fill=(255, 255, 255, 240), font=font)

    # Composite overlay on base image
    final_img = Image.alpha_composite(im, overlay).convert("RGB")
    buf = io.BytesIO()
    final_img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf

# ----------------- Background Scheduler Worker ----------------- #

async def execute_scheduled_post(post: dict):
    post_id = post.get("id")
    text = post.get("text", "")
    accounts = post.get("accounts", [])
    image_filename = post.get("image_filename")
    viral_booster = post.get("viral_booster", False)

    image_path = None
    if image_filename:
        test_path = CARD_IMAGES_DIR / image_filename
        if test_path.exists():
            image_path = test_path

    chunks = split_text_into_chunks(text)
    posted_tweets = []

    for acc_name in accounts:
        cookie_path = COOKIES_DIR / f"{acc_name}.json"
        if not cookie_path.exists():
            continue
        try:
            client = Client(language="en-US")
            client.load_cookies(str(cookie_path))

            media_ids = []
            if image_path:
                media_id = await client.upload_media(str(image_path))
                media_ids.append(media_id)

            if media_ids:
                tweet = await client.create_tweet(text=chunks[0], media_ids=media_ids)
            else:
                tweet = await client.create_tweet(text=chunks[0])

            if hasattr(tweet, "id") and tweet.id:
                posted_tweets.append({"account": acc_name, "tweet_id": tweet.id})

            # Auto-threading for >280 chars
            last_id = tweet.id if hasattr(tweet, "id") else None
            for reply_chunk in chunks[1:]:
                await asyncio.sleep(1.5)
                reply = await client.create_tweet(text=reply_chunk, reply_to=last_id)
                last_id = reply.id if hasattr(reply, "id") else last_id

            await asyncio.sleep(2)
        except Exception as e:
            print(f"Scheduled post failed on @{acc_name}: {e}")

    # If viral booster is active, auto-retweet & like across other accounts
    if viral_booster and posted_tweets:
        first_tweet = posted_tweets[0]
        t_id = first_tweet["tweet_id"]
        main_acc = first_tweet["account"]
        asyncio.create_task(run_viral_booster(t_id, main_acc))

async def run_viral_booster(tweet_id: str, origin_account: str):
    """Likes and retweets tweet_id from all other accounts"""
    accounts = get_saved_accounts()
    for acc in accounts:
        uname = acc.get("username", "")
        if uname == origin_account:
            continue
        cookie_path = COOKIES_DIR / f"{uname}.json"
        if not cookie_path.exists():
            continue
        try:
            client = Client(language="en-US")
            client.load_cookies(str(cookie_path))
            await asyncio.sleep(random.uniform(2.0, 5.0))
            await client.favorite_tweet(tweet_id)
            await asyncio.sleep(random.uniform(1.5, 3.5))
            await client.retweet(tweet_id)
        except Exception as e:
            print(f"Booster error on @{uname}: {e}")

async def execute_autopilot_cycle():
    config = get_autopilot_config()
    add_autopilot_log("🤖 Auto-Pilot cycle shuru hua...", "info")
    
    # 1. Generate viral article
    article = generate_viral_article()
    add_autopilot_log(f"📝 Naya viral article create kiya: '{article['title']}'", "info")
    
    # 2. Publish to Blogger via email
    blogger_email = config.get("blogger_email", "itsanshu.unofficial.king1801@blogger.com")
    sender_email = config.get("sender_email", "").strip()
    sender_pass = config.get("sender_password", "").strip()
    blog_url = config.get("blog_url", "https://kingtrendz007.blogspot.com").strip()
    
    blog_post_url = blog_url
    if sender_email and sender_pass:
        try:
            success = send_blogger_email(
                to_email=blogger_email,
                subject=article["title"],
                html_content=article["content_html"],
                sender_email=sender_email,
                sender_password=sender_pass,
                smtp_server=config.get("smtp_server", "smtp.gmail.com"),
                smtp_port=int(config.get("smtp_port", 587))
            )
            if success:
                add_autopilot_log(f"✅ Blogger par auto-publish ho gaya: {blogger_email}", "success")
                await asyncio.sleep(8)
                # Try fetching latest post link from Blogger feed
                try:
                    feed_url = f"{blog_url.rstrip('/')}/feeds/posts/default?alt=json"
                    async with httpx.AsyncClient(timeout=10.0) as http_client:
                        resp = await http_client.get(feed_url)
                        if resp.status_code == 200:
                            data = resp.json()
                            entries = data.get("feed", {}).get("entry", [])
                            if entries:
                                for lk in entries[0].get("link", []):
                                    if lk.get("rel") == "alternate":
                                        blog_post_url = lk.get("href")
                                        break
                except Exception as fe:
                    print(f"Feed error: {fe}")
            else:
                add_autopilot_log("⚠️ Blogger email publish fail hua, direct blog URL use ho raha hai.", "warning")
        except Exception as e:
            add_autopilot_log(f"⚠️ Email error: {e}", "warning")
    else:
        add_autopilot_log("ℹ️ Sender email set nahi hai, direct blog link use kiya ja raha hai.", "info")

    # 3. Create Fake Video Card
    card_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    dest_path = CARD_IMAGES_DIR / f"{card_id}.jpg"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            r = await http_client.get(article["image_url"])
            if r.status_code == 200:
                async with aiofiles.open(dest_path, "wb") as f:
                    await f.write(r.content)
    except Exception as img_err:
        print(f"Image error: {img_err}")
        
    card = {
        "id": card_id,
        "target_url": blog_post_url,
        "title": article["title"],
        "description": "Watch full video & read story",
        "duration": article.get("duration", "04:15"),
        "play_button": True,
        "image_filename": f"{card_id}.jpg" if dest_path.exists() else None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    cards = get_saved_cards()
    cards.insert(0, card)
    save_cards(cards)
    
    short_url = f"/v/{card_id}"
    add_autopilot_log(f"🎬 Fake Video Card generate ho gaya (Card ID: {card_id})", "success")

    # 4. Broadcast across all Twitter Accounts
    accounts = get_saved_accounts()
    first_tweet_id = None
    first_username = None
    posted_count = 0
    
    if accounts:
        for acc in accounts:
            uname = acc.get("username")
            cookie_path = COOKIES_DIR / f"{uname}.json"
            if not cookie_path.exists():
                continue
            try:
                client = Client(language="en-US")
                client.load_cookies(str(cookie_path))
                tweet_text = f"🚨 {article['title']}\n\nWatch full uncut clip before it gets removed 👇🔞\n{short_url}?acc={uname}"
                tweet = await client.create_tweet(text=tweet_text)
                if hasattr(tweet, "id") and tweet.id:
                    posted_count += 1
                    if not first_tweet_id:
                        first_tweet_id = tweet.id
                        first_username = uname
                await asyncio.sleep(random.uniform(2.0, 4.0))
            except Exception as te:
                print(f"Twitter post error on @{uname}: {te}")
                
        if posted_count > 0:
            add_autopilot_log(f"🚀 {posted_count} Twitter accounts par successfully broadcast ho gaya!", "success")
            if config.get("top_reply_pinner", True) and first_tweet_id:
                asyncio.create_task(run_viral_booster(str(first_tweet_id), first_username))
    else:
        add_autopilot_log("⚠️ Koi Twitter account connect nahi hai! Posting skip hui.", "warning")

    # 5. VIP Comment Hijacker
    if config.get("vip_hijacker_enabled", True) and accounts:
        vip_targets = config.get("vip_targets", ["elonmusk", "MrBeast", "FabrizioRomano"])
        add_autopilot_log(f"🥷 VIP Hijacker checking {', '.join(vip_targets[:3])}...", "info")
        hijack_user = accounts[0]["username"]
        cookie_path = COOKIES_DIR / f"{hijack_user}.json"
        if cookie_path.exists():
            try:
                h_client = Client(language="en-US")
                h_client.load_cookies(str(cookie_path))
                for target_vip in vip_targets[:3]:
                    try:
                        u_obj = await h_client.get_user_by_screen_name(target_vip)
                        if u_obj and hasattr(u_obj, "id"):
                            t_list = await h_client.get_user_tweets(u_obj.id, "Tweets", count=3)
                            if t_list and len(t_list) > 0:
                                v_tweet = t_list[0]
                                reply_text = f"Nobody is talking about what happened at 02:15 in this viral clip 😭🔞👇 {short_url}?acc={hijack_user}"
                                rep = await h_client.create_tweet(text=reply_text, reply_to=v_tweet.id)
                                add_autopilot_log(f"🎯 @{target_vip} ke tweet par auto-comment drop ho gaya!", "success")
                                if hasattr(rep, "id") and rep.id:
                                    asyncio.create_task(run_viral_booster(str(rep.id), hijack_user))
                                break
                    except Exception as ve:
                        print(f"VIP target @{target_vip} err: {ve}")
            except Exception as he:
                print(f"Hijacker error: {he}")

    config["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_autopilot_config(config)
    add_autopilot_log("🎉 Auto-Pilot cycle complete! Agla cycle interval ke baad chalega.", "success")

async def scheduler_loop():
    last_autopilot_time = 0
    while True:
        try:
            await asyncio.sleep(20)
            now = datetime.now()
            now_iso = now.isoformat()
            
            # 1. Scheduled posts
            posts = get_scheduled_posts()
            changed = False
            for p in posts:
                if p.get("status") == "pending" and p.get("scheduled_time", "") <= now_iso:
                    p["status"] = "processing"
                    save_scheduled_posts(posts)
                    await execute_scheduled_post(p)
                    p["status"] = "completed"
                    p["completed_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
            if changed:
                save_scheduled_posts(posts)

            # 2. Autopilot check
            cfg = get_autopilot_config()
            if cfg.get("enabled", False):
                interval_secs = max(int(cfg.get("interval_minutes", 120)) * 60, 600)
                current_time = now.timestamp()
                if (current_time - last_autopilot_time) >= interval_secs:
                    last_autopilot_time = current_time
                    await execute_autopilot_cycle()
        except Exception as e:
            print(f"Scheduler loop error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(scheduler_loop())

# ----------------- Frontend & Core Auth ----------------- #

@app.get("/", response_class=HTMLResponse)
async def home_page():
    return HTMLResponse(content=get_index_html())

@app.post("/api/verify-pin")
async def verify_pin(pin: str = Form(...)):
    if pin.strip() == DEFAULT_PIN:
        return {"success": True, "message": "Access Granted"}
    return JSONResponse(status_code=401, content={"success": False, "message": "Galat Security PIN! Access Denied."})

@app.get("/api/accounts")
async def list_accounts(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Galat PIN hai!")
    return {"accounts": get_saved_accounts()}

@app.post("/api/accounts/add")
async def add_account(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    pin: str = Form(...)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN! Unauthorized."})

    username_clean = username.strip().lstrip("@")
    cookie_path = COOKIES_DIR / f"{username_clean}.json"

    client = Client(language="en-US")
    try:
        await client.login(
            auth_info_1=username_clean,
            auth_info_2=email.strip(),
            password=password.strip()
        )
        client.save_cookies(str(cookie_path))

        accounts = get_saved_accounts()
        accounts = [acc for acc in accounts if acc.get("username") != username_clean]
        accounts.append({
            "username": username_clean,
            "email": email.strip()
        })
        save_accounts(accounts)

        return {"success": True, "message": f"@{username_clean} successfully connect ho gaya!"}
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"Login fail hua: {str(e)}"}
        )

@app.post("/api/accounts/delete")
async def delete_account(username: str = Form(...), pin: str = Form(...)):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    username_clean = username.strip().lstrip("@")
    cookie_path = COOKIES_DIR / f"{username_clean}.json"
    if cookie_path.exists():
        cookie_path.unlink()

    accounts = get_saved_accounts()
    accounts = [acc for acc in accounts if acc.get("username") != username_clean]
    save_accounts(accounts)

    return {"success": True, "message": f"@{username_clean} ko remove kar diya gaya."}

BULK_IMPORT_STATUS = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "imported": 0,
    "failed": 0,
    "results": [],
    "message": ""
}

async def process_bulk_import_worker(lines: list):
    global BULK_IMPORT_STATUS
    BULK_IMPORT_STATUS["is_running"] = True
    BULK_IMPORT_STATUS["total"] = len(lines)
    BULK_IMPORT_STATUS["current"] = 0
    BULK_IMPORT_STATUS["imported"] = 0
    BULK_IMPORT_STATUS["failed"] = 0
    BULK_IMPORT_STATUS["results"] = []
    BULK_IMPORT_STATUS["message"] = f"0 / {len(lines)} accounts process ho rahe hain..."

    for idx, raw_line in enumerate(lines):
        BULK_IMPORT_STATUS["current"] = idx + 1
        
        # Detect delimiter (colon, comma, pipe, or tab)
        if ":" in raw_line:
            parts = [p.strip() for p in raw_line.split(":")]
        elif "|" in raw_line:
            parts = [p.strip() for p in raw_line.split("|")]
        elif "," in raw_line:
            parts = [p.strip() for p in raw_line.split(",")]
        else:
            parts = raw_line.split()

        if len(parts) < 3:
            BULK_IMPORT_STATUS["failed"] += 1
            BULK_IMPORT_STATUS["results"].insert(0, {
                "username": raw_line[:20],
                "status": "failed",
                "message": "Format galat hai (username:email:password)"
            })
            continue

        uname = parts[0].lstrip("@").strip()
        p1 = parts[1].strip()
        p2 = parts[2].strip()

        if "@" in p1:
            email = p1
            pwd = p2
        elif "@" in p2:
            pwd = p1
            email = p2
        else:
            email = p1
            pwd = p2

        cookie_path = COOKIES_DIR / f"{uname}.json"
        client = Client(language="en-US")
        try:
            await client.login(auth_info_1=uname, auth_info_2=email, password=pwd)
            client.save_cookies(str(cookie_path))

            saved_accs = get_saved_accounts()
            saved_accs = [acc for acc in saved_accs if acc.get("username") != uname]
            saved_accs.append({
                "username": uname,
                "email": email
            })
            save_accounts(saved_accs)
            BULK_IMPORT_STATUS["imported"] += 1
            BULK_IMPORT_STATUS["results"].insert(0, {
                "username": uname,
                "status": "success",
                "message": "Connected successfully"
            })
        except Exception as e:
            BULK_IMPORT_STATUS["failed"] += 1
            err_str = str(e)
            if "suspended" in err_str.lower():
                clean_err = "Account Suspended by Twitter"
            elif "password" in err_str.lower() or "credentials" in err_str.lower() or "401" in err_str:
                clean_err = "Galat Password / Credentials"
            elif "rate limit" in err_str.lower() or "429" in err_str:
                clean_err = "Rate Limit (thoda intezar karein)"
            else:
                clean_err = err_str[:60]
            BULK_IMPORT_STATUS["results"].insert(0, {
                "username": uname,
                "status": "failed",
                "message": clean_err
            })

        BULK_IMPORT_STATUS["results"] = BULK_IMPORT_STATUS["results"][:150]
        BULK_IMPORT_STATUS["message"] = f"{BULK_IMPORT_STATUS['current']} / {len(lines)} accounts done ({BULK_IMPORT_STATUS['imported']} connected)"
        await asyncio.sleep(1.0)

    BULK_IMPORT_STATUS["is_running"] = False
    BULK_IMPORT_STATUS["message"] = f"Bulk Import Finished! {BULK_IMPORT_STATUS['imported']} connected, {BULK_IMPORT_STATUS['failed']} failed."

@app.post("/api/accounts/bulk-start")
@app.post("/api/accounts/bulk-add")
async def bulk_start_accounts(
    pin: str = Form(...),
    accounts_text: str = Form(""),
    accounts_file: Optional[UploadFile] = File(None)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    content = ""
    if accounts_file and accounts_file.filename:
        file_bytes = await accounts_file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif accounts_text.strip():
        content = accounts_text.strip()

    if not content:
        return JSONResponse(status_code=400, content={"success": False, "message": "Koi account data nahi mila!"})

    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return JSONResponse(status_code=400, content={"success": False, "message": "Valid lines nahi mili."})

    if BULK_IMPORT_STATUS.get("is_running", False):
        return {"success": True, "already_running": True, "total": BULK_IMPORT_STATUS.get("total", len(lines)), "message": "Import pehle se chal raha hai!"}

    asyncio.create_task(process_bulk_import_worker(lines))
    return {
        "success": True,
        "total": len(lines),
        "message": f"{len(lines)} accounts background import shuru ho gaya!"
    }

@app.get("/api/accounts/bulk-status")
async def bulk_status_endpoint(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"success": True, "status": BULK_IMPORT_STATUS}

@app.post("/api/accounts/clean-suspended")
async def clean_suspended_accounts(pin: str = Form(...)):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    accounts = get_saved_accounts()
    if not accounts:
        return {"success": True, "removed_count": 0, "active_count": 0, "message": "Koi account connect nahi hai."}

    removed_accounts = []
    active_accounts = []

    for acc in accounts:
        uname = acc.get("username", "")
        cookie_path = COOKIES_DIR / f"{uname}.json"
        if not cookie_path.exists():
            removed_accounts.append({"username": uname, "reason": "Cookie file missing"})
            continue

        try:
            client = Client(language="en-US")
            client.load_cookies(str(cookie_path))
            u = await client.get_user_by_screen_name(uname)
            if u and hasattr(u, "id"):
                active_accounts.append(acc)
            else:
                removed_accounts.append({"username": uname, "reason": "Account Suspended / Not Found"})
                if cookie_path.exists():
                    try:
                        cookie_path.unlink()
                    except Exception:
                        pass
        except Exception as e:
            err_msg = str(e).lower()
            if "suspend" in err_msg or "401" in err_msg or "unauthorized" in err_msg or "not found" in err_msg or "deactivated" in err_msg or "restricted" in err_msg:
                removed_accounts.append({"username": uname, "reason": "Suspended / Unauthorized"})
                if cookie_path.exists():
                    try:
                        cookie_path.unlink()
                    except Exception:
                        pass
            else:
                # Keep if temporary connection glitch
                active_accounts.append(acc)

        await asyncio.sleep(0.5)

    save_accounts(active_accounts)
    msg = f"{len(removed_accounts)} suspended/dead accounts remove kar diye gaye! {len(active_accounts)} accounts ready hain."
    return {
        "success": True,
        "removed_count": len(removed_accounts),
        "active_count": len(active_accounts),
        "removed": removed_accounts,
        "message": msg
    }

# ----------------- Fake Video Overlay Card & Cloaker ----------------- #

@app.post("/api/cards/create")
async def create_video_card(
    request: Request,
    target_url: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    duration: str = Form("04:15"),
    play_button: bool = Form(True),
    image_url: str = Form(""),
    image_file: Optional[UploadFile] = File(None),
    pin: str = Form(...)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN! Unauthorized."})

    target_url = target_url.strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = "https://" + target_url

    # Generate random 6-character card ID
    card_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    saved_img_filename = f"{card_id}.jpg"
    dest_path = CARD_IMAGES_DIR / saved_img_filename

    # Save uploaded image or fetch remote image
    if image_file and image_file.filename:
        content = await image_file.read()
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(content)
    elif image_url.strip():
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(image_url.strip())
                if resp.status_code == 200:
                    async with aiofiles.open(dest_path, "wb") as f:
                        await f.write(resp.content)
        except Exception as e:
            print(f"Could not download image from url: {e}")

    # Build card object
    base_url = str(request.base_url).rstrip("/")
    card = {
        "id": card_id,
        "target_url": target_url,
        "title": title.strip(),
        "description": description.strip() or "Click to watch full video",
        "duration": duration.strip(),
        "play_button": play_button,
        "image_filename": saved_img_filename if dest_path.exists() else None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    cards = get_saved_cards()
    cards.insert(0, card)
    save_cards(cards)

    short_url = f"{base_url}/v/{card_id}"
    return {
        "success": True,
        "card_id": card_id,
        "short_url": short_url,
        "message": "Fake Video Card successfully ban gaya!"
    }

@app.get("/v/img/{card_id}")
async def serve_card_image(card_id: str):
    cards = get_saved_cards()
    card = next((c for c in cards if c.get("id") == card_id), None)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    img_path = CARD_IMAGES_DIR / card["image_filename"] if card.get("image_filename") else None
    buf = generate_video_overlay_image(img_path, card.get("play_button", True), card.get("duration", "04:15"))
    return StreamingResponse(buf, media_type="image/jpeg")

@app.get("/v/{card_id}")
async def handle_card_click(request: Request, card_id: str, acc: Optional[str] = None):
    cards = get_saved_cards()
    card = next((c for c in cards if c.get("id") == card_id), None)
    if not card:
        return HTMLResponse("<h1>Link not found or expired.</h1>", status_code=404)

    user_agent = request.headers.get("user-agent", "").lower()
    crawler_keywords = ["twitterbot", "telegrambot", "facebookexternalhit", "whatsapp", "linkedinbot", "discordbot", "slackbot", "curl", "bot", "crawler", "preview"]

    base_url = str(request.base_url).rstrip("/")
    img_url = f"{base_url}/v/img/{card_id}"

    # If crawler, serve OpenGraph / Twitter Card preview tags
    if any(k in user_agent for k in crawler_keywords):
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{card['title']}</title>
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{card['title']}">
  <meta name="twitter:description" content="{card['description']}">
  <meta name="twitter:image" content="{img_url}">
  <meta property="og:type" content="video.other">
  <meta property="og:title" content="{card['title']}">
  <meta property="og:description" content="{card['description']}">
  <meta property="og:image" content="{img_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="675">
</head>
<body>
  <p>Redirecting to video...</p>
</body>
</html>"""
        return HTMLResponse(content=html_content)

    # Real human click: Track click!
    client_ip = request.client.host if request.client else ""
    record_click(card_id=card_id, account=acc, ip=client_ip, user_agent=user_agent)

    # Immediate redirect to target URL
    target = card["target_url"]
    redirect_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="refresh" content="0;url={target}">
  <script>window.location.replace("{target}");</script>
</head>
<body style="background:#000;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;">
  <p>Opening video...</p>
</body>
</html>"""
    return HTMLResponse(content=redirect_html)

# ----------------- Click Analytics API ----------------- #

@app.get("/api/analytics/clicks")
async def get_analytics(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    clicks_data = get_clicks_data()
    cards = get_saved_cards()

    # Enrich cards with their click counts
    cards_with_clicks = []
    for c in cards:
        c_copy = dict(c)
        c_copy["clicks"] = clicks_data.get("by_card", {}).get(c["id"], 0)
        cards_with_clicks.append(c_copy)

    return {
        "total_clicks": clicks_data.get("total_clicks", 0),
        "by_account": clicks_data.get("by_account", {}),
        "cards": cards_with_clicks,
        "recent": clicks_data.get("recent", [])[:30]
    }

@app.post("/api/analytics/reset")
async def reset_analytics(pin: str = Form(...)):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})
    save_json_file(CLICKS_FILE, {"total_clicks": 0, "by_account": {}, "by_card": {}, "recent": []})
    return {"success": True, "message": "Click stats reset ho gaye!"}

# ----------------- Worldwide Trending Hashtags ----------------- #

@app.get("/api/trends/worldwide")
async def get_worldwide_trends(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Try live fetch via twikit if accounts exist
    accounts = get_saved_accounts()
    if accounts:
        cookie_path = COOKIES_DIR / f"{accounts[0]['username']}.json"
        if cookie_path.exists():
            try:
                client = Client(language="en-US")
                client.load_cookies(str(cookie_path))
                trends = await client.get_trends("worldwide")
                if trends:
                    formatted = []
                    for t in trends[:12]:
                        name = getattr(t, "name", str(t))
                        count = getattr(t, "tweet_count", None)
                        formatted.append({
                            "name": name,
                            "volume": f"{count:,} tweets" if count else "Trending Globally"
                        })
                    return {"success": True, "trends": formatted}
            except Exception:
                pass

    # Dynamic global viral topics pool (refreshes dynamically with fresh topics on every click)
    GLOBAL_TRENDS_POOL = [
        ("#BreakingNews", "850K"), ("#ViralVideo", "620K"), ("#WorldNews", "430K"),
        ("#TrendingNow", "390K"), ("#MegaSale", "280K"), ("#ExclusiveDeal", "195K"),
        ("#AI", "740K"), ("#ChatGPT", "510K"), ("#TechNews", "310K"),
        ("#Football", "890K"), ("#ChampionsLeague", "470K"), ("#Cricket", "680K"),
        ("#Entertainment", "340K"), ("#Bollywood", "290K"), ("#Hollywood", "410K"),
        ("#Crypto", "530K"), ("#Bitcoin", "670K"), ("#Ethereum", "320K"),
        ("#Gaming", "460K"), ("#GTA6", "820K"), ("#PlayStation", "280K"),
        ("#FlashOffer", "165K"), ("#DiscountCode", "140K"), ("#Giveaway", "390K"),
        ("#FitnessMotivation", "180K"), ("#MusicAwards", "350K"), ("#BoxOffice", "220K"),
        ("#SpaceX", "410K"), ("#NASA", "260K"), ("#ElonMusk", "980K"),
        ("#TrendingIndia", "340K"), ("#ViralReels", "490K"), ("#ShoppingSpree", "175K"),
        ("#StartupIndia", "130K"), ("#MotivationDaily", "210K"), ("#MemeMonday", "380K")
    ]
    sampled = random.sample(GLOBAL_TRENDS_POOL, min(14, len(GLOBAL_TRENDS_POOL)))
    formatted_trends = [
        {"name": name, "volume": f"{vol} tweets"} for name, vol in sampled
    ]
    return JSONResponse(
        content={"success": True, "trends": formatted_trends},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

# ----------------- Schedule Posting API ----------------- #

@app.post("/api/schedule/create")
async def create_scheduled_post(
    text: str = Form(...),
    accounts: str = Form(...),
    scheduled_time: str = Form(...),
    viral_booster: bool = Form(False),
    pin: str = Form(...),
    image: Optional[UploadFile] = File(None)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    target_accs = [a.strip() for a in accounts.split(",") if a.strip()]
    if not target_accs:
        return JSONResponse(status_code=400, content={"success": False, "message": "Kam se kam ek account select karein."})

    saved_img_name = None
    if image and image.filename:
        suffix = Path(image.filename).suffix or ".jpg"
        temp_img_name = f"sched_{int(datetime.now().timestamp())}_{random.randint(100,999)}{suffix}"
        dest_path = CARD_IMAGES_DIR / temp_img_name
        content = await image.read()
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(content)
        saved_img_name = temp_img_name

    job_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    new_job = {
        "id": job_id,
        "text": text.strip(),
        "accounts": target_accs,
        "scheduled_time": scheduled_time.strip(),
        "viral_booster": viral_booster,
        "image_filename": saved_img_name,
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    posts = get_scheduled_posts()
    posts.insert(0, new_job)
    save_scheduled_posts(posts)

    return {"success": True, "message": f"Post schedule ho gaya! Exact {scheduled_time} par live ho jayega."}

@app.get("/api/schedule/list")
async def list_scheduled_posts(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"scheduled": get_scheduled_posts()}

@app.post("/api/schedule/delete")
async def delete_scheduled_post(job_id: str = Form(...), pin: str = Form(...)):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})
    posts = get_scheduled_posts()
    posts = [p for p in posts if p.get("id") != job_id]
    save_scheduled_posts(posts)
    return {"success": True, "message": "Scheduled post cancel kar diya gaya."}

# ----------------- Real-Time Live Sequential Post ----------------- #

@app.post("/api/post-single")
async def post_single(
    username: str = Form(...),
    text: str = Form(...),
    pin: str = Form(...),
    viral_booster: bool = Form(False),
    image: Optional[UploadFile] = File(None)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN! Unauthorized."})

    clean_user = username.strip().lstrip("@")
    cookie_path = COOKIES_DIR / f"{clean_user}.json"

    if not cookie_path.exists():
        return JSONResponse(status_code=400, content={"success": False, "message": "Session cookie nahi mili! Account remove karke dobara jodein."})

    chunks = split_text_into_chunks(text)
    saved_image_path = None
    if image and image.filename:
        suffix = Path(image.filename).suffix or ".jpg"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        async with aiofiles.open(temp_file.name, "wb") as f:
            content = await image.read()
            await f.write(content)
        saved_image_path = temp_file.name

    try:
        client = Client(language="en-US")
        client.load_cookies(str(cookie_path))

        media_ids = []
        if saved_image_path:
            media_id = await client.upload_media(saved_image_path)
            media_ids.append(media_id)

        if media_ids:
            tweet = await client.create_tweet(text=chunks[0], media_ids=media_ids)
        else:
            tweet = await client.create_tweet(text=chunks[0])

        last_tweet_id = tweet.id if hasattr(tweet, 'id') else None
        for reply_chunk in chunks[1:]:
            await asyncio.sleep(1.5)
            reply_tweet = await client.create_tweet(text=reply_chunk, reply_to=last_tweet_id)
            last_tweet_id = reply_tweet.id if hasattr(reply_tweet, 'id') else last_tweet_id

        # Trigger viral booster in background if selected
        if viral_booster and last_tweet_id:
            asyncio.create_task(run_viral_booster(str(last_tweet_id), clean_user))

        msg = f"Successfully Posted! ({len(chunks)} tweets thread)" if len(chunks) > 1 else "Successfully Posted!"
        return {"success": True, "message": msg, "tweet_id": str(last_tweet_id) if last_tweet_id else None}

    except Exception as e:
        err_str = str(e)
        clean_err = err_str
        if "Could not authenticate" in err_str or "401" in err_str or "Unauthorized" in err_str:
            clean_err = "Login Session Expire ho gaya hai! Account remove karke dobara connect karein."
        elif "Rate limit" in err_str or "429" in err_str:
            clean_err = "Twitter Rate Limit exceed ho gayi! Kripya 15 minute baad try karein."
        elif "duplicate" in err_str.lower():
            clean_err = "Duplicate Post! Yeh tweet pehle hi ho chuka hai. Text thoda badal kar dalein."
        elif "media" in err_str.lower():
            clean_err = f"Photo upload error: {err_str}"
        return JSONResponse(status_code=400, content={"success": False, "message": clean_err})

    finally:
        if saved_image_path and os.path.exists(saved_image_path):
            try:
                os.remove(saved_image_path)
            except Exception:
                pass

# ----------------- Auto-Pilot API Endpoints ----------------- #

@app.get("/api/autopilot/config")
async def get_autopilot_endpoint(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "success": True,
        "config": get_autopilot_config(),
        "logs": get_autopilot_logs()[:40]
    }

@app.post("/api/autopilot/save")
async def save_autopilot_endpoint(
    pin: str = Form(...),
    enabled: bool = Form(False),
    blog_url: str = Form("https://kingtrendz007.blogspot.com"),
    blogger_email: str = Form("itsanshu.unofficial.king1801@blogger.com"),
    sender_email: str = Form(""),
    sender_password: str = Form(""),
    interval_minutes: int = Form(120),
    vip_hijacker_enabled: bool = Form(True),
    vip_targets: str = Form("elonmusk, MrBeast, FabrizioRomano, BBCBreaking"),
    top_reply_pinner: bool = Form(True)
):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    targets = [t.strip().lstrip("@") for t in vip_targets.split(",") if t.strip()]
    cfg = get_autopilot_config()
    cfg.update({
        "enabled": enabled,
        "blog_url": blog_url.strip(),
        "blogger_email": blogger_email.strip(),
        "sender_email": sender_email.strip(),
        "sender_password": sender_password.strip() if sender_password else cfg.get("sender_password", ""),
        "interval_minutes": interval_minutes,
        "vip_hijacker_enabled": vip_hijacker_enabled,
        "vip_targets": targets,
        "top_reply_pinner": top_reply_pinner
    })
    save_autopilot_config(cfg)
    status_str = "ON (Active)" if enabled else "OFF (Disabled)"
    add_autopilot_log(f"⚙️ Auto-Pilot settings update hui (Status: {status_str})", "info")
    return {"success": True, "message": f"Auto-Pilot settings save ho gayi! (Status: {status_str})"}

@app.post("/api/autopilot/run-now")
async def run_autopilot_now_endpoint(pin: str = Form(...)):
    if pin.strip() != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})
    asyncio.create_task(execute_autopilot_cycle())
    return {"success": True, "message": "Auto-Pilot cycle background me shuru ho gaya hai!"}

@app.get("/api/autopilot/logs")
async def get_autopilot_logs_endpoint(pin: str = ""):
    if pin.strip() != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"success": True, "logs": get_autopilot_logs()[:50]}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
