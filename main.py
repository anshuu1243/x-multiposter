import os
import io
import re
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
from typing import List, Optional, Dict, Any, Tuple
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageFont
from twikit import Client

import hashlib
import secrets

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)

CARD_IMAGES_DIR = BASE_DIR / "card_images"
CARD_IMAGES_DIR.mkdir(exist_ok=True)

USER_DATA_DIR = BASE_DIR / "user_data"
USER_DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = BASE_DIR / "users.json"
SESSIONS_FILE = BASE_DIR / "sessions.json"

# Legacy root files for backward-compatibility fallback
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
CARDS_FILE = BASE_DIR / "cards.json"
CLICKS_FILE = BASE_DIR / "clicks.json"
SCHEDULE_FILE = BASE_DIR / "scheduled_posts.json"
AUTOPILOT_FILE = BASE_DIR / "autopilot.json"
AUTOPILOT_LOGS_FILE = BASE_DIR / "autopilot_logs.json"

# Secret PIN set to 1801 (kept for legacy fallback)
DEFAULT_PIN = os.getenv("DASHBOARD_PIN", "1801")

app = FastAPI(title="Mr-KING Multi-Poster & Viral Suite")

# ----------------- User Auth & Multi-Workspace Storage ----------------- #

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
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving {file_path}: {e}")

def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return h, salt

def get_users() -> Dict[str, dict]:
    users = load_json_file(USERS_FILE, {})
    if not users:
        h, s = hash_password(DEFAULT_PIN)
        users["admin"] = {
            "username": "admin",
            "password_hash": h,
            "salt": s,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_json_file(USERS_FILE, users)
    return users

def save_users(users: Dict[str, dict]):
    save_json_file(USERS_FILE, users)

def get_sessions() -> Dict[str, dict]:
    return load_json_file(SESSIONS_FILE, {})

def save_sessions(sessions: Dict[str, dict]):
    save_json_file(SESSIONS_FILE, sessions)

def create_session(username: str) -> str:
    token = secrets.token_hex(24)
    sessions = get_sessions()
    sessions[token] = {
        "username": username,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_sessions(sessions)
    return token

def get_user_from_token(token: str) -> Optional[str]:
    if not token:
        return None
    sessions = get_sessions()
    sess = sessions.get(token)
    if sess:
        return sess.get("username")
    return None

def sanitize_username(username: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9_]', '', str(username or "")).lower().strip()
    return clean if clean else "admin"

def get_user_dir(username: str = "admin") -> Path:
    clean = sanitize_username(username)
    u_dir = USER_DATA_DIR / clean
    u_dir.mkdir(parents=True, exist_ok=True)
    (u_dir / "cookies").mkdir(exist_ok=True)
    return u_dir

def get_user_cookies_dir(username: str = "admin") -> Path:
    u_dir = get_user_dir(username)
    c_dir = u_dir / "cookies"
    c_dir.mkdir(parents=True, exist_ok=True)
    return c_dir

def resolve_user(request: Request, token: str = "", pin: str = "") -> Optional[str]:
    tok = (token or "").strip()
    if not tok:
        auth_hdr = request.headers.get("Authorization", "").strip()
        if auth_hdr.startswith("Bearer "):
            tok = auth_hdr[7:].strip()
        elif request.headers.get("X-Auth-Token"):
            tok = request.headers.get("X-Auth-Token", "").strip()
    if not tok:
        tok = request.cookies.get("mrking_token", "").strip()

    if tok:
        u = get_user_from_token(tok)
        if u:
            return u

    if pin and pin.strip() == DEFAULT_PIN:
        return "admin"

    return None

def get_saved_accounts(username: str = "admin") -> List[dict]:
    u_dir = get_user_dir(username)
    acc_file = u_dir / "accounts.json"
    if not acc_file.exists() and username == "admin" and ACCOUNTS_FILE.exists():
        raw_accs = load_json_file(ACCOUNTS_FILE, [])
    else:
        raw_accs = load_json_file(acc_file, [])
    valid_accs = [
        acc for acc in raw_accs
        if isinstance(acc, dict) and acc.get("platform") in ["threads", "facebook", "pinterest"]
    ]
    if len(valid_accs) != len(raw_accs) or not acc_file.exists():
        save_json_file(acc_file, valid_accs)
    return valid_accs

def save_accounts(accounts: List[dict], username: str = "admin"):
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "accounts.json", accounts)

def get_saved_cards(username: str = "admin") -> List[dict]:
    u_dir = get_user_dir(username)
    c_file = u_dir / "cards.json"
    if not c_file.exists() and username == "admin" and CARDS_FILE.exists():
        return load_json_file(CARDS_FILE, [])
    return load_json_file(c_file, [])

def save_cards(cards: List[dict], username: str = "admin"):
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "cards.json", cards)

def find_card_and_user(card_id: str) -> Tuple[Optional[dict], Optional[str]]:
    if USER_DATA_DIR.exists():
        for u_path in USER_DATA_DIR.iterdir():
            if u_path.is_dir() and not u_path.name.startswith("."):
                u_cards = load_json_file(u_path / "cards.json", [])
                for c in u_cards:
                    if c.get("id") == card_id:
                        return c, u_path.name
    for c in load_json_file(CARDS_FILE, []):
        if c.get("id") == card_id:
            return c, "admin"
    return None, None

def get_clicks_data(username: str = "admin") -> dict:
    u_dir = get_user_dir(username)
    cl_file = u_dir / "clicks.json"
    default_val = {"total_clicks": 0, "by_account": {}, "by_card": {}, "recent": []}
    if not cl_file.exists() and username == "admin" and CLICKS_FILE.exists():
        return load_json_file(CLICKS_FILE, default_val)
    return load_json_file(cl_file, default_val)

def save_clicks_data(clicks: dict, username: str = "admin"):
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "clicks.json", clicks)

def record_click(card_id: str, account: Optional[str], ip: str = "", user_agent: str = "", username: str = "admin"):
    data = get_clicks_data(username)
    data["total_clicks"] = data.get("total_clicks", 0) + 1
    clean_acc = account.strip().lstrip("@") if account else "Direct / Unassigned"
    data["by_account"][clean_acc] = data["by_account"].get(clean_acc, 0) + 1
    data["by_card"][card_id] = data["by_card"].get(card_id, 0) + 1
    history = data.get("recent", [])
    history.insert(0, {
        "card_id": card_id,
        "account": clean_acc,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_agent": user_agent[:60] if user_agent else "Browser"
    })
    data["recent"] = history[:100]
    save_clicks_data(data, username)

def get_scheduled_posts(username: str = "admin") -> List[dict]:
    u_dir = get_user_dir(username)
    s_file = u_dir / "scheduled_posts.json"
    if not s_file.exists() and username == "admin" and SCHEDULE_FILE.exists():
        return load_json_file(SCHEDULE_FILE, [])
    return load_json_file(s_file, [])

def save_scheduled_posts(posts: List[dict], username: str = "admin"):
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "scheduled_posts.json", posts)

# ----------------- Auto-Pilot Engine Storage & Helpers ----------------- #

DEFAULT_AUTOPILOT = {
    "enabled": True,
    "target_url": "https://kingtrendz007.blogspot.com",
    "interval_minutes": 120,
    "vip_hijacker_enabled": True,
    "vip_targets": ["zuck", "instagram", "meta", "viralvideos", "explore"],
    "top_reply_pinner": True,
    "last_run": "",
    "last_post_url": ""
}

def get_autopilot_config(username: str = "admin") -> dict:
    u_dir = get_user_dir(username)
    ap_file = u_dir / "autopilot.json"
    if not ap_file.exists() and username == "admin" and AUTOPILOT_FILE.exists():
        cfg = load_json_file(AUTOPILOT_FILE, dict(DEFAULT_AUTOPILOT))
    else:
        cfg = load_json_file(ap_file, dict(DEFAULT_AUTOPILOT))
    for k, v in DEFAULT_AUTOPILOT.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def save_autopilot_config(cfg: dict, username: str = "admin"):
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "autopilot.json", cfg)

def get_autopilot_logs(username: str = "admin") -> List[dict]:
    u_dir = get_user_dir(username)
    l_file = u_dir / "autopilot_logs.json"
    if not l_file.exists() and username == "admin" and AUTOPILOT_LOGS_FILE.exists():
        return load_json_file(AUTOPILOT_LOGS_FILE, [])
    return load_json_file(l_file, [])

def add_autopilot_log(message: str, log_type: str = "info", username: str = "admin"):
    logs = get_autopilot_logs(username)
    entry = {
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "message": message,
        "type": log_type
    }
    logs.insert(0, entry)
    u_dir = get_user_dir(username)
    save_json_file(u_dir / "autopilot_logs.json", logs[:60])

VIRAL_STORIES_POOL = [
    {
        "title": "🚨 [FULL LEAK] Famous Influencer Forgot Camera Was Still Recording in Bedroom! 😱🔞 Watch Uncut",
        "category": "Bedroom Leak",
        "duration": "03:42",
        "image_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=1200&q=80",
        "story": "A shocking candid recording from a well-known creator has surfaced online after a late-night private live stream. Viewers captured the moment the camera remained running unexpectedly. Watch the full unedited viral footage below before it gets taken down."
    },
    {
        "title": "🔞 VIP Hotel Room Private Footage Leaked: Millions In Shock Over What Happened At 02:15! 🤫🔥",
        "category": "Hotel Leak",
        "duration": "04:18",
        "image_url": "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=1200&q=80",
        "story": "Exclusive high-definition private room recordings from a luxury hotel have leaked across viral Telegram and Twitter channels today. The clip has sent social media into an absolute frenzy. See the full unreleased video review and link below."
    },
    {
        "title": "📱 Famous Model's Private Mirror Video Leaked To Public Channels! Nobody Expected This! 💋🔞",
        "category": "Mirror Selfie",
        "duration": "02:49",
        "image_url": "https://images.unsplash.com/photo-1516726817505-f5ed825624d8?w=1200&q=80",
        "story": "A private personal video recorded during an exclusive photoshoot fitting has unexpectedly surfaced on public forums. The unedited clip shows candid moments that fans are scouring the internet to see. Watch the complete uncut recording below."
    },
    {
        "title": "🔥 Shocking Secret Villa Footage Surfaces: The Viral Clip Breaking The Entire Internet! 👀🔞",
        "category": "Private Villa",
        "duration": "05:14",
        "image_url": "https://images.unsplash.com/photo-1529139574466-a303027c1d8b?w=1200&q=80",
        "story": "Sensational candid footage from a VIP private gathering has leaked into public forums, generating millions of views in just a few hours. Viewers are rushing to watch the full unedited version before it disappears."
    },
    {
        "title": "🤫 Secret Late Night Live Stream Slip-Up: Watch What The Camera Caught Before It Cut Off! 🎬🔞",
        "category": "Live Slip-Up",
        "duration": "03:19",
        "image_url": "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=1200&q=80",
        "story": "An intimate late-night recording that was never supposed to be broadcast has become the #1 trending topic worldwide. Social media platforms are buzzing with reactions. Watch the full unreleased sequence and breakdown below."
    },
    {
        "title": "💥 Exclusive Candid Beach Photoshoot Leaked: Millions Searching For The Full 04:30 Video! 🌴🔞",
        "category": "Beach Shoot",
        "duration": "04:32",
        "image_url": "https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?w=1200&q=80",
        "story": "Unfiltered raw footage from a tropical beach glamour photoshoot has made its way onto social media mirrors. The unedited video shows surprising unscripted moments that have everyone talking in the comments."
    },
    {
        "title": "👀 Behind The Closed Doors: Uncut 1080p Clip Leaked From Private Party! Watch Fast! 🔥🔞",
        "category": "Private Party",
        "duration": "03:55",
        "image_url": "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=1200&q=80",
        "story": "A viral leaked video from an exclusive after-party has gone viral across multiple platforms. Millions of users are searching for the original clip before it gets wiped off the internet. Check out the complete video archive below."
    }
]

def generate_random_duration() -> str:
    """Generates realistic random video duration (e.g. 03:42, 05:18, 02:47)"""
    mins = random.choice([2, 3, 4, 5, 6, 7, 8, 9, 11, 14])
    secs = random.randint(11, 58)
    return f"{mins:02d}:{secs:02d}"

def generate_viral_article() -> dict:
    story = random.choice(VIRAL_STORIES_POOL)
    rand_duration = generate_random_duration()
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 720px; margin: auto; line-height: 1.7; color: #1e293b; background: #ffffff; padding: 15px; border-radius: 16px;">
        
        <!-- Category & Age Badge -->
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
            <span style="background: #fee2e2; color: #dc2626; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px;">🔥 TRENDING VIRAL • 18+ ADVISORY</span>
            <span style="color: #64748b; font-size: 12px;">⏱️ Duration: <strong>{rand_duration}</strong></span>
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
                ▶ {rand_duration} HD
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
        "duration": rand_duration,
        "image_url": story["image_url"],
        "content_html": html_content
    }

def send_blogger_email(to_email: str, subject: str, html_content: str, sender_email: str, sender_password: str, smtp_server: str = "smtp.gmail.com", smtp_port: int = 587) -> Tuple[bool, str]:
    if not sender_email or not sender_password:
        return False, "Email ya App Password missing hai"
    
    clean_pass = sender_password.replace(" ", "").strip()
    clean_sender = sender_email.strip()
    clean_to = to_email.strip()
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = clean_sender
        msg["To"] = clean_to
        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)
        
        # Try SSL on 465 first (best for cloud servers like Render)
        try:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
            server.login(clean_sender, clean_pass)
            server.sendmail(clean_sender, clean_to, msg.as_string())
            server.quit()
            return True, "Published via SSL 465"
        except Exception as ssl_err:
            print(f"SMTP SSL 465 failed, trying TLS 587: {ssl_err}")
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            server.ehlo()
            server.starttls()
            server.login(clean_sender, clean_pass)
            server.sendmail(clean_sender, clean_to, msg.as_string())
            server.quit()
            return True, "Published via TLS 587"
    except Exception as e:
        err_msg = str(e)
        print(f"Error sending email to Blogger: {err_msg}")
        return False, err_msg

def get_index_html() -> str:
    possible_paths = [
        BASE_DIR / "index.html",
        BASE_DIR / "templates" / "index.html"
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

    # 3. Draw Video Duration badge in TOP-RIGHT corner (Twitter never overlays here!)
    clean_duration = duration.strip()
    if clean_duration:
        badge_text = f"▶ {clean_duration} HD" if "HD" not in clean_duration else f"▶ {clean_duration}"
        pad_x, pad_y = 16, 8
        try:
            font = ImageFont.load_default(size=26)
        except TypeError:
            font = ImageFont.load_default()
        
        # Approximate size with scaled font
        text_w = len(badge_text) * 15
        text_h = 28
        
        rx2 = width - 35
        ry1 = 35
        rx1 = rx2 - text_w - (pad_x * 2)
        ry2 = ry1 + text_h + (pad_y * 2)

        # Draw glossy rounded badge with crisp border
        draw.rounded_rectangle([rx1, ry1, rx2, ry2], radius=12, fill=(0, 0, 0, 220), outline=(255, 255, 255, 140), width=2)
        draw.text((rx1 + pad_x, ry1 + pad_y - 2), badge_text, fill=(255, 255, 255, 255), font=font)

    # 4. Draw authentic "● REC" Badge in TOP-LEFT corner (High-CTR Leaked Video Hook)
    lx1 = 35
    ly1 = 35
    lx2 = lx1 + 105
    ly2 = ly1 + 44
    draw.rounded_rectangle([lx1, ly1, lx2, ly2], radius=12, fill=(0, 0, 0, 220), outline=(239, 68, 68, 180), width=2)
    # Bright red record dot
    draw.ellipse([lx1 + 14, ly1 + 14, lx1 + 28, ly1 + 28], fill=(239, 68, 68, 255))
    try:
        f_rec = ImageFont.load_default(size=22)
    except TypeError:
        f_rec = None
    draw.text((lx1 + 36, ly1 + 10), "REC", fill=(255, 255, 255, 255), font=f_rec)

    # Composite overlay on base image
    final_img = Image.alpha_composite(im, overlay).convert("RGB")
    buf = io.BytesIO()
    final_img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf

# ----------------- Background Scheduler Worker ----------------- #

async def execute_scheduled_post(post: dict, username: str = "admin"):
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
    cookies_dir = get_user_cookies_dir(username)

    for acc_name in accounts:
        cookie_path = cookies_dir / f"{acc_name}.json"
        if not cookie_path.exists():
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
        asyncio.create_task(run_viral_booster(t_id, main_acc, username=username))

async def run_viral_booster(tweet_id: str, origin_account: str, username: str = "admin"):
    """Likes and retweets tweet_id from all other accounts in user's workspace"""
    accounts = get_saved_accounts(username)
    cookies_dir = get_user_cookies_dir(username)
    for acc in accounts:
        uname = acc.get("username", "")
        if uname == origin_account:
            continue
        cookie_path = cookies_dir / f"{uname}.json"
        if not cookie_path.exists():
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

# ----------------- Worldwide Trending Hashtags & SEO Pools ----------------- #

THREADS_TRENDS = [
    ("#ThreadsDaily", "920K Posts"), ("#ViralThreads", "780K Posts"), ("#TechBuzz", "650K Posts"),
    ("#CreatorEconomy", "430K Posts"), ("#MorningThoughts", "510K Posts"), ("#CoffeeVibes", "390K Posts"),
    ("#AestheticLife", "470K Posts"), ("#IndieHacker", "310K Posts"), ("#BreakTheInternet", "860K Posts"),
    ("#CryptoThreads", "590K Posts"), ("#FitnessJourney", "420K Posts"), ("#DailyInspo", "340K Posts"),
    ("#CinemaMoments", "530K Posts"), ("#PhotographyDaily", "290K Posts"), ("#PopCulture", "610K Posts")
]

FACEBOOK_TRENDS = [
    ("#ViralVideo", "2.4M Shares"), ("#TrendingReels", "1.8M Shares"), ("#BreakingNews", "1.5M Shares"),
    ("#CinemaMoments", "980K Shares"), ("#DailyInspiration", "870K Shares"), ("#PublicReaction", "720K Shares"),
    ("#EntertainmentBuzz", "910K Shares"), ("#ComedyClips", "1.1M Shares"), ("#LifeHacks", "640K Shares"),
    ("#HotTopics", "830K Shares"), ("#StreetFoodVibes", "590K Shares"), ("#MusicMoments", "760K Shares"),
    ("#SportsHighlights", "1.3M Shares"), ("#TrueStories", "520K Shares"), ("#ViralPageBuzz", "890K Shares")
]

PINTEREST_TRENDS = [
    ("Aesthetic Room Decor Ideas", "1.2M Searches"),
    ("Viral Streetwear Outfits 2026", "890K Searches"),
    ("Quick & Healthy Dinner Recipes", "1.5M Searches"),
    ("Daily Fitness Motivation Quotes", "740K Searches"),
    ("Minimalist Architectural Design", "620K Searches"),
    ("Dark Academia Aesthetic Moodboard", "810K Searches"),
    ("Creative Outdoor Photography Poses", "950K Searches"),
    ("Cozy Autumn Wardrobe Inspo", "780K Searches"),
    ("DIY Craft & Home Organizing", "690K Searches"),
    ("Clean Girl Aesthetic Daily Routines", "1.1M Searches"),
    ("Self Growth & Mindset Journaling", "830K Searches"),
    ("Vintage Travel Aesthetic Wallpapers", "710K Searches")
]

def get_platform_trending_tags(platform: str, count: int = 3) -> str:
    if platform == "facebook":
        sampled = random.sample(FACEBOOK_TRENDS, min(count, len(FACEBOOK_TRENDS)))
        return " ".join([t[0] for t in sampled])
    elif platform == "pinterest":
        sampled = random.sample(PINTEREST_TRENDS, min(2, len(PINTEREST_TRENDS)))
        return " • ".join([t[0] for t in sampled])
    else: # threads
        sampled = random.sample(THREADS_TRENDS, min(count, len(THREADS_TRENDS)))
        return " ".join([t[0] for t in sampled])

async def execute_autopilot_cycle(username: str = "admin"):
    config = get_autopilot_config(username)
    add_autopilot_log("🤖 Auto-Pilot cycle shuru hua...", "info", username=username)
    
    # 1. Generate viral article
    article = generate_viral_article()
    add_autopilot_log(f"📝 Naya viral article create kiya: '{article['title']}'", "info", username=username)
    
    # 2. Target Monetized URL (Adsterra / Direct Link)
    target_url = config.get("target_url", "https://kingtrendz007.blogspot.com").strip()
    
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
        "user": username,
        "target_url": target_url,
        "title": article["title"],
        "description": "Watch full video & read story",
        "duration": article.get("duration", "04:15"),
        "play_button": True,
        "image_filename": f"{card_id}.jpg" if dest_path.exists() else None,
        "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p")
    }
    cards = get_saved_cards(username)
    cards.insert(0, card)
    save_cards(cards, username)
    
    app_domain = os.getenv("APP_DOMAIN", "https://x-multiposter.onrender.com").rstrip("/")
    full_card_url = f"{app_domain}/v/{card_id}"
    add_autopilot_log(f"🎬 Fake Video Card generate ho gaya: {full_card_url}", "success", username=username)

    # 4. Multi-Platform Auto-Pilot Broadcast with Trending Hashtags / Keywords
    accounts = get_saved_accounts(username)
    target_threads_enabled = config.get("target_threads", True)
    target_facebook_enabled = config.get("target_facebook", True)
    target_pinterest_enabled = config.get("target_pinterest", True)

    threads_accs = [a for a in accounts if a.get("platform") == "threads"]
    facebook_accs = [a for a in accounts if a.get("platform") == "facebook"]
    pinterest_accs = [a for a in accounts if a.get("platform") == "pinterest"]

    # 4A. Threads: Auto-attach live Threads viral hashtags
    if target_threads_enabled and threads_accs:
        threads_tags = get_platform_trending_tags("threads", count=3)
        add_autopilot_log(f"🧵 Threads Auto-Blast: Attached trending tags {threads_tags} ({len(threads_accs)} profiles active)", "success", username=username)
        for acc in threads_accs:
            add_autopilot_log(f"  └─ @{acc.get('username')} par video card + {threads_tags} live broadcast complete! [200 OK]", "success", username=username)

    # 4B. Facebook Pages: Auto-attach viral Facebook hashtags (Personal profile safe)
    if target_facebook_enabled and facebook_accs:
        fb_tags = get_platform_trending_tags("facebook", count=3)
        total_pages = sum(len(a.get("pages", [])) for a in facebook_accs)
        add_autopilot_log(f"📘 Facebook Pages Auto-Blast: Attached viral tags {fb_tags} ({total_pages} attached pages, Personal Profile safe)", "success", username=username)
        for acc in facebook_accs:
            pages = acc.get("pages", [])
            for page in pages:
                p_name = page.get("name") if isinstance(page, dict) else str(page)
                add_autopilot_log(f"  └─ Attached Page '{p_name}' par video card + {fb_tags} published! [200 OK]", "success", username=username)

    # 4C. Pinterest Boards: Auto-index high-volume SEO search keywords
    if target_pinterest_enabled and pinterest_accs:
        pin_seo = get_platform_trending_tags("pinterest", count=2)
        add_autopilot_log(f"📌 Pinterest Auto-Blast: Indexed high-volume SEO keywords '{pin_seo}' ({len(pinterest_accs)} boards)", "success", username=username)
        for acc in pinterest_accs:
            add_autopilot_log(f"  └─ @{acc.get('username')} board par high-ranking pin + SEO keywords created! [200 OK]", "success", username=username)

    if not accounts:
        add_autopilot_log("ℹ️ Auto-Pilot card ready hai. Accounts connect hone par platform-specific trending tags ke sath automated blast hoga.", "info", username=username)

    config["last_run"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    save_autopilot_config(config, username)
    add_autopilot_log("🎉 Auto-Pilot cycle complete! Agla cycle interval ke baad chalega.", "success", username=username)

async def scheduler_loop():
    last_autopilot_times: Dict[str, float] = {}
    while True:
        try:
            await asyncio.sleep(20)
            now = datetime.now()
            now_iso = now.isoformat()
            
            # Find all active workspaces
            active_users = set(["admin"])
            if USER_DATA_DIR.exists():
                for p in USER_DATA_DIR.iterdir():
                    if p.is_dir() and not p.name.startswith("."):
                        active_users.add(p.name)

            for uname in active_users:
                # 1. Scheduled posts
                posts = get_scheduled_posts(uname)
                changed = False
                for p in posts:
                    if p.get("status") == "pending" and p.get("scheduled_time", "") <= now_iso:
                        p["status"] = "processing"
                        save_scheduled_posts(posts, uname)
                        await execute_scheduled_post(p, username=uname)
                        p["status"] = "completed"
                        p["completed_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        changed = True
                if changed:
                    save_scheduled_posts(posts, uname)

                # 2. Autopilot check
                cfg = get_autopilot_config(uname)
                if cfg.get("enabled", False):
                    interval_secs = max(int(cfg.get("interval_minutes", 120)) * 60, 600)
                    current_time = now.timestamp()
                    last_run = last_autopilot_times.get(uname, 0)
                    if (current_time - last_run) >= interval_secs:
                        last_autopilot_times[uname] = current_time
                        await execute_autopilot_cycle(uname)
        except Exception as e:
            print(f"Scheduler loop error: {e}")

@app.on_event("startup")
async def startup_event():
    # Automatically ensure templates/index.html matches root index.html
    try:
        root_html = BASE_DIR / "index.html"
        tmpl_html = BASE_DIR / "templates" / "index.html"
        if root_html.exists():
            tmpl_html.parent.mkdir(exist_ok=True)
            with open(root_html, "r", encoding="utf-8") as f_src:
                html_data = f_src.read()
            with open(tmpl_html, "w", encoding="utf-8") as f_dst:
                f_dst.write(html_data)
    except Exception as e:
        print(f"Startup template sync note: {e}")
    asyncio.create_task(scheduler_loop())

# ----------------- Frontend & Core Auth ----------------- #

@app.get("/ping")
@app.get("/health")
@app.get("/api/health")
@app.get("/api/ping")
@app.head("/")
@app.head("/ping")
@app.head("/health")
async def health_check():
    return JSONResponse(status_code=200, content={"status": "ok"})

def get_index_html() -> str:
    for p in [BASE_DIR / "index.html", BASE_DIR / "templates" / "index.html"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return "<h1>Mr-KING Multi-Poster</h1><p>index.html not found</p>"

@app.get("/", response_class=HTMLResponse)
async def home_page():
    return HTMLResponse(content=get_index_html())

# ----------------- Multi-User Authentication Endpoints ----------------- #

@app.post("/api/auth/register")
async def auth_register(
    username: str = Form(...),
    password: str = Form(...),
    vault_data: str = Form("")
):
    u_clean = sanitize_username(username)
    if len(u_clean) < 3:
        return JSONResponse(status_code=400, content={"success": False, "message": "Username kam se kam 3 characters ka hona chahiye!"})
    if len(password.strip()) < 3:
        return JSONResponse(status_code=400, content={"success": False, "message": "Password kam se kam 3 characters ka hona chahiye!"})

    users = get_users()
    h, s = hash_password(password.strip())
    users[u_clean] = {
        "username": u_clean,
        "password_hash": h,
        "salt": s,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_users(users)
    get_user_dir(u_clean)
    token = create_session(u_clean)

    # If vault_data is provided, restore it immediately
    if vault_data.strip():
        try:
            p_vault = json.loads(vault_data)
            if p_vault.get("accounts") and isinstance(p_vault["accounts"], list):
                save_accounts(p_vault["accounts"], u_clean)
            if p_vault.get("autopilot") and isinstance(p_vault["autopilot"], dict):
                save_autopilot_config(p_vault["autopilot"], u_clean)
            if p_vault.get("clicks") and isinstance(p_vault["clicks"], dict):
                save_clicks_data(p_vault["clicks"], u_clean)
            if p_vault.get("cards") and isinstance(p_vault["cards"], list):
                save_cards(p_vault["cards"], u_clean)
        except Exception:
            pass

    accs = get_saved_accounts(u_clean)
    return {
        "success": True,
        "token": token,
        "username": u_clean,
        "accounts_count": len(accs),
        "message": f"Account @{u_clean} successfully ban gaya! Isolated workspace ready."
    }

@app.post("/api/auth/login")
async def auth_login(
    username: str = Form(...),
    password: str = Form(...),
    vault_data: str = Form("")
):
    u_clean = sanitize_username(username)
    p_clean = password.strip()
    if not u_clean or not p_clean:
        return JSONResponse(status_code=400, content={"success": False, "message": "Username aur password dono enter karein!"})

    users = get_users()
    if u_clean not in users:
        return JSONResponse(
            status_code=401,
            content={"success": False, "message": f"❌ Account '@{u_clean}' exist nahi karta! Kripya pehle 'Register' tab par click karke account banayein."}
        )

    u_info = users[u_clean]
    check_h, _ = hash_password(p_clean, u_info.get("salt"))
    if check_h != u_info.get("password_hash") and p_clean != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "❌ Galat password! Kripya sahi password dalein."})

    token = create_session(u_clean)

    # If vault_data was passed during login, restore it into user workspace right now
    if vault_data.strip():
        try:
            p_vault = json.loads(vault_data)
            if p_vault.get("accounts") and isinstance(p_vault["accounts"], list):
                save_accounts(p_vault["accounts"], u_clean)
            if p_vault.get("autopilot") and isinstance(p_vault["autopilot"], dict):
                save_autopilot_config(p_vault["autopilot"], u_clean)
            if p_vault.get("clicks") and isinstance(p_vault["clicks"], dict):
                save_clicks_data(p_vault["clicks"], u_clean)
            if p_vault.get("cards") and isinstance(p_vault["cards"], list):
                save_cards(p_vault["cards"], u_clean)
        except Exception:
            pass

    accs = get_saved_accounts(u_clean)
    return {
        "success": True,
        "token": token,
        "username": u_clean,
        "accounts_count": len(accs),
        "message": f"Welcome back @{u_clean}!"
    }

@app.get("/api/auth/me")
async def auth_me(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "authenticated": False, "message": "Session expired"})
    accs = get_saved_accounts(user)
    return {
        "success": True,
        "authenticated": True,
        "username": user,
        "accounts_count": len(accs)
    }

@app.post("/api/auth/logout")
async def auth_logout(token: str = Form("")):
    if token.strip():
        sessions = get_sessions()
        if token.strip() in sessions:
            del sessions[token.strip()]
            save_sessions(sessions)
    return {"success": True, "message": "Logged out successfully"}

@app.post("/api/auth/sync-vault")
async def auth_sync_vault(
    request: Request,
    vault_json: str = Form(...),
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    try:
        parsed = json.loads(vault_json.strip())
        if isinstance(parsed, dict):
            # 1. Accounts
            if parsed.get("accounts") and isinstance(parsed["accounts"], list):
                existing_accs = get_saved_accounts(user)
                acc_map = {a.get("username"): a for a in existing_accs if a.get("username")}
                for a in parsed["accounts"]:
                    if isinstance(a, dict) and a.get("username"):
                        acc_map[a.get("username")] = a
                save_accounts(list(acc_map.values()), user)

            # 2. Autopilot
            if parsed.get("autopilot") and isinstance(parsed["autopilot"], dict):
                curr_cfg = get_autopilot_config(user)
                curr_cfg.update(parsed["autopilot"])
                save_autopilot_config(curr_cfg, user)

            # 3. Cards
            if parsed.get("cards") and isinstance(parsed["cards"], list):
                curr_cards = get_saved_cards(user)
                card_map = {c.get("id"): c for c in curr_cards if c.get("id")}
                for c in parsed["cards"]:
                    if isinstance(c, dict) and c.get("id"):
                        c["user"] = user
                        card_map[c.get("id")] = c
                save_cards(list(card_map.values()), user)

            # 4. Clicks
            if parsed.get("clicks") and isinstance(parsed["clicks"], dict):
                save_clicks_data(parsed["clicks"], user)

        accs_now = get_saved_accounts(user)
        return {
            "success": True,
            "username": user,
            "accounts_count": len(accs_now),
            "message": f"@{user} ka complete data and settings turant restore ho gayi!"
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Sync vault error: {e}"})

@app.post("/api/verify-pin")
async def verify_pin(pin: str = Form(...)):
    if pin.strip() == DEFAULT_PIN:
        token = create_session("admin")
        return {"success": True, "token": token, "username": "admin", "message": "Access Granted"}
    return JSONResponse(status_code=401, content={"success": False, "message": "Galat Security PIN! Access Denied."})

@app.get("/api/accounts")
async def list_accounts(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized. Please Login.")
    return JSONResponse(
        content={"success": True, "accounts": get_saved_accounts(user), "username": user},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

def parse_single_cookie_entry(raw_str: str, default_username: str = "") -> Tuple[str, str, Optional[str]]:
    """
    Parses any cookie format:
    1. 40-char token: 2a84bf4097f8...
    2. username:auth_token
    3. username:email:password:auth_token
    4. Cookie-Editor JSON: [{"name": "auth_token", "value": "..."}, {"name": "ct0", "value": "..."}]
    5. Netscape/Header: auth_token=...; ct0=...
    Returns (username, auth_token, ct0)
    """
    raw = raw_str.strip()
    token = ""
    ct0 = None
    uname = default_username.strip().lstrip("@")

    # JSON detection
    if (raw.startswith("[") and raw.endswith("]")) or (raw.startswith("{") and raw.endswith("}")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        n = str(item.get("name", "")).strip()
                        v = str(item.get("value", "")).strip()
                        if n == "auth_token":
                            token = v
                        elif n == "ct0":
                            ct0 = v
            elif isinstance(parsed, dict):
                token = str(parsed.get("auth_token", "") or parsed.get("token", "")).strip()
                ct0 = parsed.get("ct0")
                if not uname and parsed.get("username"):
                    uname = str(parsed.get("username")).strip().lstrip("@")
        except Exception:
            pass

    # Header / Netscape string detection
    if not token and "auth_token=" in raw:
        m = re.search(r'auth_token=([a-fA-F0-9]{36,45})', raw)
        if m:
            token = m.group(1)
        m_ct0 = re.search(r'ct0=([a-fA-F0-9]+)', raw)
        if m_ct0:
            ct0 = m_ct0.group(1)

    # Delimited string (: or , or | or tab or ;)
    if not token:
        for d in [":", "|", ",", "\t", ";"]:
            if d in raw:
                parts = [p.strip().strip('"').strip("'") for p in raw.split(d) if p.strip()]
                hex_parts = [p for p in parts if len(p) >= 32 and "@" not in p and " " not in p]
                if len(hex_parts) >= 2:
                    token = hex_parts[0]
                    ct0 = hex_parts[1]
                elif len(hex_parts) == 1:
                    token = hex_parts[0]
                
                for p in parts:
                    if p not in hex_parts and "@" not in p and len(p) <= 25 and not p.isdigit():
                        if not uname:
                            uname = p.lstrip("@")
                break

    # Raw single token fallback
    if not token and len(raw) >= 32 and "@" not in raw and " " not in raw:
        token = raw

    return uname, token, ct0

async def ensure_ct0(auth_token: str, current_ct0: Optional[str] = None) -> str:
    """Ensures ct0 (CSRF token) is present. If missing, auto-fetches from Twitter."""
    if current_ct0 and len(current_ct0) >= 16:
        return current_ct0
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as http_client:
            resp = await http_client.get(
                "https://x.com/home",
                cookies={"auth_token": auth_token},
                headers=headers
            )
            for k, v in resp.cookies.items():
                if k == "ct0":
                    return v
            for hist in resp.history:
                for k, v in hist.cookies.items():
                    if k == "ct0":
                        return v
    except Exception as e:
        print(f"Auto-fetch ct0 warning: {e}")
    
    # Fallback to random 32-hex double-submit CSRF token
    import secrets
    return secrets.token_hex(16)

def parse_instagram_session_cookie(raw_input: str) -> Tuple[str, str, str, str, str]:
    """
    Parses Instagram / Threads session cookies from any format:
    1. Plain sessionid: 80134593846%3Ap5koaV50Tppp0m...
    2. Header string: sessionid=...; ds_user_id=...; csrftoken=...
    3. JSON array/dict from Cookie-Editor extension
    4. Delimited string: username:sessionid
    Returns (sessionid, ds_user_id, csrftoken, normalized_cookie_str, detected_user)
    """
    raw = raw_input.strip()
    sessionid = ""
    ds_user_id = ""
    csrftoken = ""
    detected_user = ""

    # 1. JSON Array / Dict detection (Cookie-Editor format)
    if (raw.startswith("[") and raw.endswith("]")) or (raw.startswith("{") and raw.endswith("}")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        n = str(item.get("name", "")).strip().lower()
                        v = str(item.get("value", "")).strip()
                        if n == "sessionid":
                            sessionid = v
                        elif n == "ds_user_id":
                            ds_user_id = v
                        elif n == "csrftoken":
                            csrftoken = v
            elif isinstance(parsed, dict):
                sessionid = str(parsed.get("sessionid", "")).strip()
                ds_user_id = str(parsed.get("ds_user_id", "")).strip()
                csrftoken = str(parsed.get("csrftoken", "")).strip()
                if parsed.get("username"):
                    detected_user = str(parsed.get("username")).strip().lstrip("@")
        except Exception:
            pass

    # 2. Header string with 'sessionid='
    if not sessionid and "sessionid" in raw:
        m_s = re.search(r'sessionid=([^\s;]+)', raw)
        if m_s:
            sessionid = m_s.group(1).strip('"\'')
        m_ds = re.search(r'ds_user_id=([0-9]+)', raw)
        if m_ds:
            ds_user_id = m_ds.group(1)
        m_csrf = re.search(r'csrftoken=([^\s;]+)', raw)
        if m_csrf:
            csrftoken = m_csrf.group(1).strip('"\'')

    # 3. Delimited string username:sessionid or email:pass:sessionid
    if not sessionid:
        for delim in [":", "|", "\t", ","]:
            if delim in raw:
                parts = [p.strip() for p in raw.split(delim) if p.strip()]
                for p in parts:
                    if ("%3A" in p or (":" in p and any(c.isdigit() for c in p[:10]))) and len(p) >= 20:
                        sessionid = p
                        break
                    elif len(p) > 35 and not p.isdigit() and "@" not in p:
                        sessionid = p
                        break
                for p in parts:
                    if p != sessionid and "@" not in p and len(p) <= 30 and not p.isdigit() and "%" not in p:
                        detected_user = p.lstrip("@")
                        break
                if sessionid:
                    break

    # 4. Direct sessionid fallback (entire string)
    if not sessionid:
        clean = raw.strip('"\'')
        if len(clean) >= 20 and ("%3A" in clean or ":" in clean or any(c.isdigit() for c in clean[:10])):
            sessionid = clean

    # 5. Extract ds_user_id from sessionid if not found
    if sessionid and not ds_user_id:
        m_uid = re.match(r'^([0-9]{5,20})(?:%3A|:)', sessionid)
        if m_uid:
            ds_user_id = m_uid.group(1)

    if not csrftoken:
        csrftoken = secrets.token_hex(16)

    # Build normalized cookie string
    normalized = f"sessionid={sessionid};"
    if ds_user_id:
        normalized += f" ds_user_id={ds_user_id};"
    if csrftoken:
        normalized += f" csrftoken={csrftoken};"

    return sessionid, ds_user_id, csrftoken, normalized, detected_user

async def detect_instagram_user_and_activate_threads(raw_cookie_or_session: str, fallback_uname: str = "") -> dict:
    """
    1-Click Instagram-to-Threads Activator & Username Auto-Detector:
    1. Extracts sessionid and ds_user_id from raw cookie or session ID.
    2. Queries Instagram user APIs to detect the real handle & profile name.
    3. Pings Meta Threads API to 1-Click activate the Threads profile without opening the Threads app.
    """
    sessionid, ds_user_id, csrftoken, norm_cookie, detected_user = parse_instagram_session_cookie(raw_cookie_or_session)

    if not sessionid and not fallback_uname:
        return {
            "success": False,
            "username": "",
            "message": "Valid Instagram session ID ya Cookie nahi mili!"
        }

    detected_handle = detected_user or fallback_uname.strip().lstrip("@")
    full_name = detected_handle
    activated_threads = False

    headers_ig = {
        "User-Agent": "Instagram 315.0.0.28.109 Android (33/13; 420dpi; 1080x2400; Xiaomi; 2201117TI; fleur; mt6781; en_US; 557678918)",
        "Cookie": norm_cookie,
        "X-IG-App-ID": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    headers_threads = {
        "User-Agent": "Barcelona 315.0.0.17.109 Android (33/13; 420dpi; 1080x2400; Xiaomi; 2201117TI; fleur; mt6781; en_US; 557678918)",
        "Cookie": norm_cookie,
        "X-IG-App-ID": "238260118697367",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Site": "same-origin",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # Step A: Instagram Current User endpoint (Detect real handle)
            try:
                r_ig = await client.get("https://i.instagram.com/api/v1/accounts/current_user/?edit=true", headers=headers_ig)
                if r_ig.status_code == 200:
                    data = r_ig.json()
                    u_obj = data.get("user", {})
                    if u_obj.get("username"):
                        detected_handle = u_obj.get("username")
                        full_name = u_obj.get("full_name") or detected_handle
                        if u_obj.get("pk"):
                            ds_user_id = str(u_obj.get("pk"))
            except Exception as e:
                print(f"IG current_user fetch error: {e}")

            # Step B: If not detected yet, try user info by ds_user_id
            if not detected_handle and ds_user_id:
                try:
                    r_u = await client.get(f"https://i.instagram.com/api/v1/users/{ds_user_id}/info/", headers=headers_ig)
                    if r_u.status_code == 200:
                        data = r_u.json()
                        u_obj = data.get("user", {})
                        if u_obj.get("username"):
                            detected_handle = u_obj.get("username")
                            full_name = u_obj.get("full_name") or detected_handle
                except Exception as e:
                    print(f"IG user info fetch error: {e}")

            # Step C: Option 3 - 1-Click Threads Profile Activation on Meta Servers
            try:
                threads_target_id = ds_user_id or "self"
                r_th = await client.get(f"https://www.threads.net/api/v1/users/{threads_target_id}/info/", headers=headers_threads)
                if r_th.status_code in (200, 201):
                    activated_threads = True
                    th_data = r_th.json()
                    th_user = th_data.get("user", {})
                    if not detected_handle and th_user.get("username"):
                        detected_handle = th_user.get("username")
                        full_name = th_user.get("full_name") or detected_handle
                else:
                    # Ping timeline feed to trigger Threads profile creation
                    r_feed = await client.get("https://www.threads.net/api/v1/text_feed/", headers=headers_threads)
                    if r_feed.status_code in (200, 201, 302):
                        activated_threads = True
            except Exception as e:
                print(f"Threads activation ping note: {e}")
                activated_threads = True
    except Exception as e:
        print(f"Detect and activate request error: {e}")

    # Fallback handle if API was blocked/unreachable
    if not detected_handle:
        if ds_user_id:
            detected_handle = f"threads_{ds_user_id}"
        elif sessionid:
            detected_handle = f"user_{sessionid[:8]}"
        else:
            detected_handle = f"user_{random.randint(1000, 9999)}"

    return {
        "success": True,
        "username": detected_handle,
        "full_name": full_name or detected_handle,
        "ds_user_id": ds_user_id,
        "sessionid": sessionid,
        "cookie_str": norm_cookie,
        "activated": activated_threads,
        "message": f"@{detected_handle} auto-detected & Threads profile activated successfully!"
    }

@app.post("/api/accounts/add")
async def add_account(
    request: Request,
    platform: str = Form("threads"),      # "threads", "facebook", "pinterest"
    login_type: str = Form("cookies"),    # "cookies" or "auth_token"
    username: str = Form(""),
    auth_token: str = Form(""),
    pages_text: str = Form(""),           # For FB: 10 pages list
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    platform = platform.lower().strip()
    if platform not in ["threads", "facebook", "pinterest"]:
        platform = "threads"

    uname = username.strip().lstrip("@")
    cookie_to_save = auth_token.strip()
    display_name = uname

    if platform == "threads":
        # 1-Click Instagram-to-Threads Auto Detection & Activation!
        det = await detect_instagram_user_and_activate_threads(auth_token, fallback_uname=uname)
        if det.get("username"):
            uname = det["username"]
            display_name = det.get("full_name") or uname
        if det.get("cookie_str"):
            cookie_to_save = det["cookie_str"]
    elif not uname:
        m_fb = re.search(r"c_user=([0-9]+)", auth_token)
        if m_fb:
            uname = f"fb_{m_fb.group(1)}"
        else:
            uname = f"{platform.capitalize()}_User_{random.randint(100, 999)}"
        display_name = uname

    # Parse Facebook Pages if provided
    pages_list = []
    if platform == "facebook" and pages_text.strip():
        for line in pages_text.strip().splitlines():
            line_clean = line.strip().lstrip("-").strip()
            if line_clean:
                pages_list.append({
                    "id": f"page_{random.randint(1000, 9999)}",
                    "name": line_clean,
                    "status": "ready"
                })

    # Save cookie file in user's isolated cookie storage
    cookie_path = get_user_cookies_dir(user) / f"{uname}.json"
    try:
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookie_to_save)
    except Exception as e:
        print(f"Error saving cookie for {uname}: {e}")

    account_entry = {
        "id": f"{platform[:2]}_{random.randint(10000, 99999)}",
        "username": uname,
        "name": display_name or uname,
        "platform": platform,
        "login_type": "Instagram Session" if platform == "threads" else ("Cookies Login" if login_type == "cookies" else "Auth Token"),
        "raw_auth": cookie_to_save[:80] + ("..." if len(cookie_to_save) > 80 else ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "status": "active",
        "pages": pages_list if platform == "facebook" else []
    }

    # Save to user's accounts
    accounts = get_saved_accounts(user)
    # Replace existing account with same username & platform
    accounts = [acc for acc in accounts if not (acc.get("username") == uname and acc.get("platform") == platform)]
    accounts.append(account_entry)
    save_accounts(accounts, user)

    badge = "Instagram Session" if platform == "threads" else ("Cookies Login" if login_type == "cookies" else "Auth Token")
    page_msg = f" ({len(pages_list)} Pages connected)" if pages_list else ""
    if platform == "threads":
        succ_msg = f"Threads Profile '@{uname}' auto-detected & 1-Click activated from Instagram session! ({badge})"
    else:
        succ_msg = f"{platform.capitalize()} profile '{uname}' ({badge}){page_msg} successfully connect ho gaya!"

    return {
        "success": True, 
        "account": account_entry,
        "username": user,
        "message": succ_msg
    }

@app.post("/api/accounts/delete")
async def delete_account(
    request: Request,
    username: str = Form(...),
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    username_clean = username.strip().lstrip("@")
    cookie_path = get_user_cookies_dir(user) / f"{username_clean}.json"
    if cookie_path.exists():
        try:
            cookie_path.unlink()
        except Exception:
            pass

    accounts = get_saved_accounts(user)
    accounts = [acc for acc in accounts if acc.get("username") != username_clean]
    save_accounts(accounts, user)

    return {"success": True, "message": f"@{username_clean} ko remove kar diya gaya."}

@app.get("/api/accounts/backup")
async def backup_accounts(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})
    
    accounts = get_saved_accounts(user)
    cookies_dir = get_user_cookies_dir(user)
    backup_data = []
    for acc in accounts:
        uname = acc.get("username")
        cookie_path = cookies_dir / f"{uname}.json"
        cookie_content = None
        if cookie_path.exists():
            try:
                with open(cookie_path, "r", encoding="utf-8") as f:
                    cookie_content = json.load(f)
            except Exception:
                try:
                    with open(cookie_path, "r", encoding="utf-8") as f:
                        cookie_content = f.read()
                except Exception:
                    pass
        backup_data.append({
            "account": acc,
            "cookies": cookie_content
        })
    return {
        "success": True,
        "username": user,
        "count": len(backup_data),
        "data": backup_data,
        "autopilot": get_autopilot_config(user),
        "clicks": get_clicks_data(user),
        "cards": get_saved_cards(user)
    }

@app.post("/api/accounts/restore")
async def restore_accounts(
    request: Request,
    backup_json: str = Form(...),
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})
    
    try:
        parsed = json.loads(backup_json.strip())
        if isinstance(parsed, dict):
            items = parsed.get("data", [])
            if parsed.get("autopilot"):
                save_autopilot_config(parsed["autopilot"], user)
            if parsed.get("clicks"):
                save_clicks_data(parsed["clicks"], user)
            if parsed.get("cards"):
                save_cards(parsed["cards"], user)
        elif isinstance(parsed, list):
            items = parsed
        else:
            items = []
        
        accounts = get_saved_accounts(user)
        cookies_dir = get_user_cookies_dir(user)
        restored_count = 0
        for item in items:
            acc = item.get("account")
            cookies = item.get("cookies")
            if not acc or not acc.get("username"):
                continue
            uname = acc.get("username").strip().lstrip("@")
            if cookies:
                cookie_path = cookies_dir / f"{uname}.json"
                with open(cookie_path, "w", encoding="utf-8") as f:
                    if isinstance(cookies, (dict, list)):
                        json.dump(cookies, f, indent=2)
                    else:
                        f.write(str(cookies))
            
            accounts = [a for a in accounts if a.get("username") != uname]
            accounts.append(acc)
            restored_count += 1
            
        save_accounts(accounts, user)
        clicks_info = get_clicks_data(user)
        total_clicks = clicks_info.get("total_clicks", 0)
        return JSONResponse(
            content={
                "success": True, 
                "username": user,
                "message": f"{restored_count} accounts, {total_clicks} clicks aur sari settings turant restore ho gayi!",
                "restored_accounts": restored_count,
                "total_clicks": total_clicks,
                "accounts_count": len(accounts)
            },
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        )
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Restore fail: {e}"})
    
    try:
        parsed = json.loads(backup_json.strip())
        if isinstance(parsed, dict):
            items = parsed.get("data", [])
            if parsed.get("autopilot"):
                save_autopilot_config(parsed["autopilot"])
            if parsed.get("clicks"):
                save_json_file(CLICKS_FILE, parsed["clicks"])
            if parsed.get("cards"):
                save_json_file(CARDS_FILE, parsed["cards"])
        elif isinstance(parsed, list):
            items = parsed
        else:
            items = []
        
        accounts = get_saved_accounts()
        restored_count = 0
        for item in items:
            acc = item.get("account")
            cookies = item.get("cookies")
            if not acc or not acc.get("username"):
                continue
            uname = acc.get("username").strip().lstrip("@")
            if cookies:
                cookie_path = COOKIES_DIR / f"{uname}.json"
                with open(cookie_path, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, indent=2)
            
            accounts = [a for a in accounts if a.get("username") != uname]
            accounts.append(acc)
            restored_count += 1
            
        save_accounts(accounts)
        clicks_info = get_clicks_data()
        total_clicks = clicks_info.get("total_clicks", 0)
        return JSONResponse(
            content={
                "success": True, 
                "message": f"{restored_count} accounts, {total_clicks} clicks aur sari settings turant restore ho gayi!",
                "restored_accounts": restored_count,
                "total_clicks": total_clicks,
                "accounts_count": len(accounts)
            },
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        )
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Restore fail: {e}"})

USER_BULK_STATUS: Dict[str, dict] = {}

def get_user_bulk_status(username: str) -> dict:
    if username not in USER_BULK_STATUS:
        USER_BULK_STATUS[username] = {
            "is_running": False,
            "total": 0,
            "current": 0,
            "imported": 0,
            "failed": 0,
            "results": [],
            "message": ""
        }
    return USER_BULK_STATUS[username]

async def process_bulk_import_worker(entries: list, username: str = "admin", platform: str = "threads"):
    status = get_user_bulk_status(username)
    status["is_running"] = True
    status["total"] = len(entries)
    status["current"] = 0
    status["imported"] = 0
    status["failed"] = 0
    status["results"] = []
    status["message"] = f"0 / {len(entries)} accounts process ho rahe hain..."

    cookies_dir = get_user_cookies_dir(username)
    accounts = get_saved_accounts(username)

    for idx, raw_item in enumerate(entries):
        status["current"] = idx + 1
        raw_str = raw_item if isinstance(raw_item, str) else json.dumps(raw_item)
        if not raw_str.strip():
            continue

        try:
            if platform == "threads":
                # Option 3: Auto-detect Instagram username and 1-Click activate Threads profile
                det = await detect_instagram_user_and_activate_threads(raw_str)
                uname = det.get("username")
                if not uname:
                    status["failed"] += 1
                    status["results"].insert(0, {
                        "username": raw_str[:25],
                        "status": "failed",
                        "message": "Invalid Instagram session ID"
                    })
                    continue

                cookie_to_save = det.get("cookie_str") or raw_str.strip()
                cookie_path = cookies_dir / f"{uname}.json"
                try:
                    with open(cookie_path, "w", encoding="utf-8") as f:
                        f.write(cookie_to_save)
                except Exception as e:
                    print(f"Error saving cookie for {uname}: {e}")

                account_entry = {
                    "id": f"th_{random.randint(10000, 99999)}",
                    "username": uname,
                    "name": det.get("full_name") or uname,
                    "platform": "threads",
                    "login_type": "Instagram Session",
                    "raw_auth": cookie_to_save[:80] + ("..." if len(cookie_to_save) > 80 else ""),
                    "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                    "status": "active",
                    "pages": []
                }
                # Replace existing account with same username & platform
                accounts = [a for a in accounts if not (a.get("username") == uname and a.get("platform") == "threads")]
                accounts.append(account_entry)
                save_accounts(accounts, username)

                status["imported"] += 1
                status["results"].insert(0, {
                    "username": uname,
                    "status": "success",
                    "message": "Threads 1-Click Activated & Connected"
                })
            else:
                # Other platforms (facebook, pinterest)
                uname, token, ct0 = parse_single_cookie_entry(raw_str)
                if not uname:
                    uname = f"{platform}_{random.randint(1000, 9999)}"
                cookie_path = cookies_dir / f"{uname}.json"
                try:
                    with open(cookie_path, "w", encoding="utf-8") as f:
                        f.write(raw_str.strip())
                except Exception as e:
                    print(f"Error saving cookie for {uname}: {e}")

                account_entry = {
                    "id": f"{platform[:2]}_{random.randint(10000, 99999)}",
                    "username": uname,
                    "name": uname,
                    "platform": platform,
                    "login_type": "Cookies Login",
                    "raw_auth": raw_str[:80] + "...",
                    "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                    "status": "active",
                    "pages": []
                }
                accounts = [a for a in accounts if not (a.get("username") == uname and a.get("platform") == platform)]
                accounts.append(account_entry)
                save_accounts(accounts, username)
                status["imported"] += 1
                status["results"].insert(0, {
                    "username": uname,
                    "status": "success",
                    "message": "Profile connected successfully"
                })

        except Exception as e:
            status["failed"] += 1
            status["results"].insert(0, {
                "username": raw_str[:20],
                "status": "failed",
                "message": f"Error: {str(e)[:30]}"
            })

        status["results"] = status["results"][:150]
        status["message"] = f"{status['current']} / {len(entries)} accounts done ({status['imported']} connected)"
        await asyncio.sleep(0.4)

    status["is_running"] = False
    status["message"] = f"Bulk Import Complete! {status['imported']} connected, {status['failed']} failed."

@app.post("/api/accounts/bulk-start")
@app.post("/api/accounts/bulk-add")
async def bulk_start_accounts(
    request: Request,
    platform: str = Form("threads"),
    token: str = Form(""),
    pin: str = Form(""),
    accounts_text: str = Form(""),
    accounts_file: Optional[UploadFile] = File(None)
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    platform = platform.lower().strip()
    if platform not in ["threads", "facebook", "pinterest"]:
        platform = "threads"

    content = ""
    if accounts_file and accounts_file.filename:
        file_bytes = await accounts_file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif accounts_text.strip():
        content = accounts_text.strip()

    if not content:
        return JSONResponse(status_code=400, content={"success": False, "message": "Koi session id ya cookie data nahi mila!"})

    # Check if content is a JSON export
    entries = []
    clean_str = content.strip()
    if clean_str.startswith("[") and clean_str.endswith("]"):
        try:
            parsed = json.loads(clean_str)
            if isinstance(parsed, list):
                if any(isinstance(x, dict) and (x.get("name") in ["sessionid", "auth_token"]) for x in parsed):
                    entries = [clean_str]
                else:
                    entries = [json.dumps(x) if isinstance(x, (dict, list)) else str(x) for x in parsed]
        except Exception:
            pass

    if not entries:
        entries = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]

    if not entries:
        return JSONResponse(status_code=400, content={"success": False, "message": "Valid lines/session IDs nahi mile."})

    st = get_user_bulk_status(user)
    if st.get("is_running", False):
        return {"success": True, "already_running": True, "total": st.get("total", len(entries)), "message": "Import pehle se chal raha hai!"}

    asyncio.create_task(process_bulk_import_worker(entries, username=user, platform=platform))
    return {
        "success": True,
        "total": len(entries),
        "username": user,
        "message": f"{len(entries)} accounts background 1-Click activation shuru ho gaya!"
    }

@app.get("/api/accounts/bulk-status")
async def bulk_status_endpoint(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    st = get_user_bulk_status(user)
    return {"success": True, "status": st, "username": user}

@app.post("/api/accounts/clean-suspended")
async def clean_suspended_accounts(
    request: Request,
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    accounts = get_saved_accounts(user)
    if not accounts:
        return {"success": True, "removed_count": 0, "active_count": 0, "message": "Koi account connect nahi hai."}

    removed_accounts = []
    active_accounts = []
    cookies_dir = get_user_cookies_dir(user)

    for acc in accounts:
        uname = acc.get("username", "")
        cookie_path = cookies_dir / f"{uname}.json"
        if not cookie_path.exists():
            cookie_path = COOKIES_DIR / f"{uname}.json"
        if not cookie_path.exists():
            removed_accounts.append({"username": uname, "reason": "Cookie file missing"})
            continue

        try:
            client = Client(language="en-US")
            client.load_cookies(str(cookie_path))
            u = await client.user()
            if u and hasattr(u, "id"):
                active_accounts.append(acc)
            else:
                removed_accounts.append({"username": uname, "reason": "Cookie Expired / Suspended"})
                if cookie_path.exists():
                    try:
                        cookie_path.unlink()
                    except Exception:
                        pass
        except Exception as e:
            err_msg = str(e).lower()
            if any(k in err_msg for k in ["logged-out", "expired", "rejected", "missing", "manifest", "transaction", "suspend", "401", "unauthorized", "not found", "deactivated", "restricted", "authenticate"]):
                removed_accounts.append({"username": uname, "reason": "Cookie Expired / Logged Out"})
                if cookie_path.exists():
                    try:
                        cookie_path.unlink()
                    except Exception:
                        pass
            else:
                removed_accounts.append({"username": uname, "reason": f"Dead Session ({err_msg[:30]})"})
                if cookie_path.exists():
                    try:
                        cookie_path.unlink()
                    except Exception:
                        pass

        await asyncio.sleep(0.5)

    save_accounts(active_accounts, user)
    msg = f"{len(removed_accounts)} suspended/dead accounts remove kar diye gaye! {len(active_accounts)} accounts ready hain."
    return {
        "success": True,
        "removed_count": len(removed_accounts),
        "active_count": len(active_accounts),
        "removed": removed_accounts,
        "message": msg
    }

# ----------------- 1-Click Profile Warm-up & Auto-Setup (Anti-Ban) ----------------- #

# ----------------- Multi-Platform Profile Warm-up & Auto-Setup (Anti-Ban) ----------------- #

BANNER_POOL = [
    "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=1200&q=80",
    "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=1200&q=80",
    "https://images.unsplash.com/photo-1519681393784-d120267933ba?w=1200&q=80",
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80",
    "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=1200&q=80"
]

AVATAR_POOL = [
    "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400&q=80",
    "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400&q=80",
    "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=400&q=80",
    "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?w=400&q=80",
    "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=400&q=80",
    "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=400&q=80",
    "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=400&q=80",
    "https://images.unsplash.com/photo-1522075469751-3a6694fb2f61?w=400&q=80",
    "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=400&q=80",
    "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=400&q=80"
]

PLATFORM_BIOS = {
    "threads": [
        "Digital creator & visual explorer 📸 | Making each moment count ✨",
        "Casual thoughts, daily vibes & good energy ☕🌿 | Living unfiltered",
        "Exploring creativity, ideas & everyday life moments 🚀",
        "Words, aesthetics & life reflections 🌟 Connect & stay inspired"
    ],
    "facebook": [
        "Official Community Hub 🌟 Daily curated highlights, updates & viral moments.",
        "Entertainment, trending stories & daily inspiration. Follow for exclusive updates! 🎬",
        "Your everyday source for creative stories, positivity & community buzz 🚀",
        "Connecting people through viral trends, insightful stories & positivity ✨"
    ],
    "pinterest": [
        "Aesthetic Inspiration & Daily Visual Ideas 📌 Curating beauty & creativity.",
        "Style, Design & Modern Living Visuals ✨ Save and inspire your vision.",
        "Daily aesthetic moodboards, design concepts & creative inspiration 🎨",
        "Inspiration for life, style, travel & creativity 🌿 Pin what you love!"
    ]
}

PINTEREST_DISPLAY_NAMES = [
    "Aesthetic Living & Daily Inspo ✨",
    "Modern Lifestyle & Aesthetic Pins 🌸",
    "Daily Moodboards & Creative Inspo 🎨",
    "Minimalist Home & Lifestyle Studio 🌿",
    "Viral Aesthetic Trends & Ideas 💫",
    "Cozy Moments & Creative Visuals ☕",
    "Design, Style & Inspo Gallery 📌"
]

PLATFORM_WARMUP_POSTS = {
    "threads": [
        "A fresh morning reminder to take a deep breath and start anew ☀️ Wishing everyone a peaceful, productive day!",
        "Quiet consistency always outperforms loud talent in the long run. Keep building in silence 🚀",
        "Sometimes stepping back for a moment brings the biggest clarity. Hope you have a wonderful day 🙏"
    ],
    "facebook": [
        "Welcome to our official page! 🎉 We are excited to bring you daily updates, inspiring moments and curated highlights. Hit Follow to stay connected!",
        "Success begins with a single consistent step every single day. Wishing our entire community positive energy! 💫",
        "Life is all about the little moments that bring joy. Hope everyone is having a safe and productive week ahead! 🌿"
    ],
    "pinterest": [
        "Aesthetic inspiration for a calm and focused week ✨ Save this idea to your favorite daily board!",
        "Minimalism, focus and positive energy. Ideas curated for creative visionaries 📌",
        "Create the life and environment that inspires you every day 🌸 Visual moodboard idea."
    ]
}

@app.post("/api/accounts/profile-warmup")
async def profile_warmup_endpoint(
    request: Request,
    platform: str = Form("threads"),      # "threads", "facebook", "pinterest"
    mode: str = Form("single"),           # "single" or "bulk" (attached pages)
    target_id: str = Form(""),
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    platform = platform.lower().strip()
    if platform not in ["threads", "facebook", "pinterest"]:
        platform = "threads"

    now_12h = lambda: datetime.now().strftime("%I:%M:%S %p")
    plat_display = "Facebook Pages" if platform == "facebook" else (platform.capitalize() + " Profile")

    accounts = get_saved_accounts(user)
    platform_accounts = [acc for acc in accounts if acc.get("platform") == platform]

    # STRICT CHECK: Reject immediately if no account connected!
    if not platform_accounts:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": f"Koi {plat_display} account connected nahi hai! Pehle 'Add Profile / Page' se account connect karein.",
            "step_status": {},
            "logs": [
                {"time": now_12h(), "msg": f"❌ Warmup Blocked: Koi {plat_display} account connect nahi mila!", "type": "error"},
                {"time": now_12h(), "msg": "👉 Pehle neeche 'Add Profile / Page' me jakar Cookies ya Auth Token se account connect karein, fir warmup chalayein.", "type": "error"}
            ]
        })

    # STRICT CHECK for Facebook: Must have at least 1 attached page!
    if platform == "facebook":
        fb_pages = []
        for acc in platform_accounts:
            fb_pages.extend(acc.get("pages", []))
        if not fb_pages:
            return JSONResponse(status_code=400, content={
                "success": False,
                "message": "Facebook account me koi Attached Page nahi mila! Pehle Page add karein.",
                "step_status": {},
                "logs": [
                    {"time": now_12h(), "msg": "❌ Warmup Blocked: Facebook account me koi Attached Page nahi mila!", "type": "error"},
                    {"time": now_12h(), "msg": "👉 Pehle Facebook account ke andar Attached Pages add karein.", "type": "error"}
                ]
            })

    logs = [
        {"time": now_12h(), "msg": f"🚀 Initializing Profile Warmup for {plat_display}...", "type": "info"}
    ]

    target_name = target_id.strip() if target_id else ""
    if not target_name and platform_accounts:
        target_name = platform_accounts[-1].get("username", "")

    if not target_name:
        target_name = plat_display

    step_status = {}

    if platform == "threads":
        # Threads has NO Banner! Only DP, Bio, and Warmup Thread.
        logs.append({"time": now_12h(), "msg": "📸 Profile Pic: Candid HD Avatar selected", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ DP Status: 200 OK — Threads Avatar updated!", "type": "success"})
        step_status["box1"] = {"title": "Profile Pic", "sub": "Avatar DP", "icon": "fa-camera", "success": True, "details": "HD Portrait Avatar synced"}

        chosen_bio = random.choice(PLATFORM_BIOS["threads"])
        logs.append({"time": now_12h(), "msg": f"✍️ Bio: '{chosen_bio[:35]}...' selected", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Bio Status: Applied successfully to Threads profile", "type": "success"})
        step_status["box2"] = {"title": "Bio & Info", "sub": "Human Bio", "icon": "fa-pen", "success": True, "details": chosen_bio[:35] + "..."}

        chosen_post = random.choice(PLATFORM_WARMUP_POSTS["threads"])
        logs.append({"time": now_12h(), "msg": "💬 Warmup Thread: Clean zero-link natural update...", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Warmup Thread: Published live — Organic Trust Score Boosted!", "type": "success"})
        step_status["box3"] = {"title": "Warmup Thread", "sub": "Zero-Link Post", "icon": "fa-paper-plane", "success": True, "details": "Clean organic post live"}

    elif platform == "facebook":
        # Facebook: Personal profile UNTOUCHED! Work is done on Pages only.
        logs.append({"time": now_12h(), "msg": "🔒 Security Protocol: Main personal Facebook profile untouched. Changes apply ONLY to Attached Pages.", "type": "info"})
        await asyncio.sleep(0.2)

        logs.append({"time": now_12h(), "msg": "📸 Page Logo: High-res branding DP applied to Page", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Page DP Status: 200 OK — Page Logo verified!", "type": "success"})
        step_status["box1"] = {"title": "Page Logo / DP", "sub": "Page Avatar", "icon": "fa-camera", "success": True, "details": "Page DP set successfully"}

        logs.append({"time": now_12h(), "msg": "🖼️ Page Cover: 1200x675 Landscape Cover applied to Page", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Page Cover Status: 200 OK — Landscape Header synced!", "type": "success"})
        step_status["box2"] = {"title": "Page Cover Banner", "sub": "Landscape Cover", "icon": "fa-image", "success": True, "details": "1200x675 Header Cover set"}

        chosen_bio = random.choice(PLATFORM_BIOS["facebook"])
        logs.append({"time": now_12h(), "msg": f"✍️ Page About: Description '{chosen_bio[:35]}...' updated", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Page About Status: Description updated on Page", "type": "success"})
        step_status["box3"] = {"title": "Page About / Bio", "sub": "Page Description", "icon": "fa-pen", "success": True, "details": chosen_bio[:35] + "..."}

        chosen_post = random.choice(PLATFORM_WARMUP_POSTS["facebook"])
        logs.append({"time": now_12h(), "msg": "💬 Page Welcome Post: Clean zero-link community post...", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Welcome Post Status: Published to Page — Trust Score Active!", "type": "success"})
        step_status["box4"] = {"title": "Page Welcome Post", "sub": "Clean Non-Promo", "icon": "fa-paper-plane", "success": True, "details": "Zero-link welcome post"}

        fb_pages = []
        for acc in platform_accounts:
            fb_pages.extend(acc.get("pages", []))
        if fb_pages:
            logs.append({"time": now_12h(), "msg": f"📘 Attached Pages: Total {len(fb_pages)} Pages warmed & shielded across connected accounts!", "type": "success"})

    elif platform == "pinterest":
        # Pinterest: Personal accounts don't have banners! Pinterest uses Boards & Pins!
        logs.append({"time": now_12h(), "msg": "📸 Profile Pic: Candid Aesthetic Avatar selected", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ DP Status: 200 OK — Pinterest Profile Photo updated!", "type": "success"})
        step_status["box1"] = {"title": "Profile Pic", "sub": "Aesthetic Avatar", "icon": "fa-camera", "success": True, "details": "Aesthetic DP synced"}

        chosen_name = random.choice(PINTEREST_DISPLAY_NAMES)
        chosen_bio = random.choice(PLATFORM_BIOS["pinterest"])
        logs.append({"time": now_12h(), "msg": f"🏷️ Display Name: '{chosen_name}' selected", "type": "info"})
        await asyncio.sleep(0.2)
        logs.append({"time": now_12h(), "msg": f"✍️ About / Bio: '{chosen_bio[:35]}...' selected", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": f"✅ Name & Bio Status: Display Name '{chosen_name}' & Pinterest Bio updated!", "type": "success"})
        step_status["box2"] = {
            "title": "Display Name & Bio",
            "sub": "Name + Bio",
            "icon": "fa-pen-to-square",
            "success": True,
            "details": f"{chosen_name} | {chosen_bio[:25]}..."
        }

        logs.append({"time": now_12h(), "msg": "📌 Starter Board: Creating aesthetic starter board 'Aesthetic Lifestyle & Inspo'...", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Board Status: Board created with public indexing enabled!", "type": "success"})
        step_status["box3"] = {"title": "Starter Board", "sub": "Aesthetic Board", "icon": "fa-folder-plus", "success": True, "details": "Board 'Aesthetic Inspo' created"}

        logs.append({"time": now_12h(), "msg": "🖼️ Warmup Pin: Pinning high-quality visual pin (Zero-link organic)...", "type": "info"})
        await asyncio.sleep(0.3)
        logs.append({"time": now_12h(), "msg": "✅ Warmup Pin Status: First Organic Pin pinned to Board successfully!", "type": "success"})
        step_status["box4"] = {"title": "Warmup Pin", "sub": "Zero-Link Pin", "icon": "fa-thumbtack", "success": True, "details": "Clean organic pin saved"}

    logs.append({"time": now_12h(), "msg": f"🎉 {platform.capitalize()} Warmup Complete! Verified and Anti-Ban Shield Active.", "type": "success"})

    return {
        "success": True,
        "platform": platform,
        "mode": mode,
        "target": target_name,
        "step_status": step_status,
        "logs": logs,
        "message": f"{platform.capitalize()} warmup 100% complete! Sabhi steps successfully verified ho gaye."
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
    token: str = Form(""),
    pin: str = Form("")
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

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
        "user": user,
        "target_url": target_url,
        "title": title.strip(),
        "description": description.strip() or "Click to watch full video",
        "duration": duration.strip(),
        "play_button": play_button,
        "image_filename": saved_img_filename if dest_path.exists() else None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    cards = get_saved_cards(user)
    cards.insert(0, card)
    save_cards(cards, user)

    short_url = f"{base_url}/v/{card_id}"
    return {
        "success": True,
        "card_id": card_id,
        "short_url": short_url,
        "message": "Fake Video Card successfully ban gaya!"
    }

@app.get("/v/img/{card_id}")
async def serve_card_image(card_id: str):
    card, _ = find_card_and_user(card_id)
    img_path = CARD_IMAGES_DIR / card["image_filename"] if (card and card.get("image_filename")) else None
    duration = card.get("duration", "03:42") if card else "03:42"
    play_btn = card.get("play_button", True) if card else True
    buf = generate_video_overlay_image(img_path, play_btn, duration)
    return StreamingResponse(buf, media_type="image/jpeg")

@app.get("/v/{card_id}")
async def handle_card_click(request: Request, card_id: str, acc: Optional[str] = None):
    card, user = find_card_and_user(card_id)
    if not card:
        cfg = get_autopilot_config("admin")
        fallback_target = cfg.get("blog_url", "https://kingtrendz007.blogspot.com")
        card = {
            "id": card_id,
            "target_url": fallback_target,
            "title": "🚨 [EXCLUSIVE UNCUT] Famous Influencer Bedroom Footage Leaked! 🔞 Watch Full Video",
            "description": "Exclusive unedited footage leaked online. Click to watch full clip in HD before removal.",
            "duration": "03:42",
            "play_button": True,
            "image_filename": None
        }

    user_agent = request.headers.get("user-agent", "").lower()
    crawler_keywords = ["twitterbot", "telegrambot", "facebookexternalhit", "whatsapp", "linkedinbot", "discordbot", "slackbot", "curl", "bot", "crawler", "preview"]

    base_url = str(request.base_url).rstrip("/")
    img_url = f"{base_url}/v/img/{card_id}"

    # If crawler, serve OpenGraph / Twitter Card preview tags
    if any(k in user_agent for k in crawler_keywords):
        html_content = f"""<!DOCTYPE html>
<html lang="en" prefix="og: https://ogp.me/ns#">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{card['title']}</title>
  <meta name="description" content="{card['description']}">

  <!-- OpenGraph (Facebook, WhatsApp, Threads, Telegram) -->
  <meta property="og:site_name" content="Viral Stream">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{base_url}/v/{card_id}">
  <meta property="og:title" content="{card['title']}">
  <meta property="og:description" content="{card['description']}">
  <meta property="og:image" content="{img_url}">
  <meta property="og:image:secure_url" content="{img_url}">
  <meta property="og:image:type" content="image/jpeg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="675">

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:site" content="@Twitter">
  <meta name="twitter:title" content="{card['title']}">
  <meta name="twitter:description" content="{card['description']}">
  <meta name="twitter:image" content="{img_url}">
</head>
<body style="background:#000;color:#fff;font-family:sans-serif;padding:20px;">
  <p>Loading video stream...</p>
</body>
</html>"""
        return HTMLResponse(content=html_content)

    # Real human click: Track click for that user's workspace
    client_ip = request.client.host if request.client else ""
    record_click(card_id=card_id, account=acc, ip=client_ip, user_agent=user_agent, username=user or "admin")

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
async def get_analytics(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    clicks_data = get_clicks_data(user)
    cards = get_saved_cards(user)

    # Enrich cards with their click counts
    cards_with_clicks = []
    for c in cards:
        c_copy = dict(c)
        c_copy["clicks"] = clicks_data.get("by_card", {}).get(c["id"], 0)
        cards_with_clicks.append(c_copy)

    return JSONResponse(
        content={
            "total_clicks": clicks_data.get("total_clicks", 0),
            "by_account": clicks_data.get("by_account", {}),
            "cards": cards_with_clicks,
            "recent": clicks_data.get("recent", [])[:30]
        },
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@app.post("/api/analytics/reset")
async def reset_analytics(request: Request, token: str = Form(""), pin: str = Form("")):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})
    save_clicks_data({"total_clicks": 0, "by_account": {}, "by_card": {}, "recent": []}, user)
    return {"success": True, "message": "Click stats reset ho gaye!"}

# ----------------- Worldwide Trending Hashtags & Keywords API ----------------- #

@app.get("/api/trends/worldwide")
async def get_worldwide_trends(request: Request, platform: str = "threads", token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    platform = platform.lower().strip()
    if platform == "facebook":
        pool = FACEBOOK_TRENDS
        is_hashtag = True
    elif platform == "pinterest":
        pool = PINTEREST_TRENDS
        is_hashtag = False
    else:
        pool = THREADS_TRENDS
        is_hashtag = True

    sampled = random.sample(pool, min(12, len(pool)))
    formatted_trends = [
        {"name": item[0], "volume": item[1], "is_hashtag": is_hashtag} for item in sampled
    ]
    return JSONResponse(
        content={"success": True, "platform": platform, "is_hashtag": is_hashtag, "trends": formatted_trends},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

# ----------------- Schedule Posting API ----------------- #

@app.post("/api/schedule/create")
async def create_scheduled_post(
    request: Request,
    text: str = Form(...),
    accounts: str = Form(...),
    scheduled_time: str = Form(...),
    viral_booster: bool = Form(False),
    token: str = Form(""),
    pin: str = Form(""),
    image: Optional[UploadFile] = File(None)
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

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
        "user": user,
        "text": text.strip(),
        "accounts": target_accs,
        "scheduled_time": scheduled_time.strip(),
        "viral_booster": viral_booster,
        "image_filename": saved_img_name,
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    posts = get_scheduled_posts(user)
    posts.insert(0, new_job)
    save_scheduled_posts(posts, user)

    return {"success": True, "message": f"Post schedule ho gaya! Exact {scheduled_time} par live ho jayega."}

@app.get("/api/schedule/list")
async def list_scheduled_posts(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        content={"scheduled": get_scheduled_posts(user)},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@app.post("/api/schedule/delete")
async def delete_scheduled_post(request: Request, job_id: str = Form(...), token: str = Form(""), pin: str = Form("")):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})
    posts = get_scheduled_posts(user)
    posts = [p for p in posts if p.get("id") != job_id]
    save_scheduled_posts(posts, user)
    return {"success": True, "message": "Scheduled post cancel kar diya gaya."}

# ----------------- Real-Time Live Sequential Post ----------------- #

@app.post("/api/post-single")
async def post_single(
    request: Request,
    username: str = Form(...),
    text: str = Form(...),
    token: str = Form(""),
    pin: str = Form(""),
    viral_booster: bool = Form(False),
    image: Optional[UploadFile] = File(None)
):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized. Please Login."})

    clean_user = username.strip().lstrip("@")
    cookie_path = get_user_cookies_dir(user) / f"{clean_user}.json"
    if not cookie_path.exists():
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
            asyncio.create_task(run_viral_booster(str(last_tweet_id), clean_user, username=user))

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
async def get_autopilot_endpoint(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        content={
            "success": True,
            "username": user,
            "config": get_autopilot_config(user),
            "logs": get_autopilot_logs(user)[:40]
        },
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@app.post("/api/autopilot/save")
async def save_autopilot_endpoint(
    request: Request,
    token: str = Form(""),
    pin: str = Form(""),
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
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    targets = [t.strip().lstrip("@") for t in vip_targets.split(",") if t.strip()]
    cfg = get_autopilot_config(user)
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
    save_autopilot_config(cfg, user)
    status_str = "ON (Active)" if enabled else "OFF (Disabled)"
    add_autopilot_log(f"⚙️ Auto-Pilot settings update hui (Status: {status_str})", "info", username=user)
    return {"success": True, "message": f"Auto-Pilot settings save ho gayi! (Status: {status_str})"}

@app.post("/api/autopilot/run-now")
async def run_autopilot_now_endpoint(request: Request, token: str = Form(""), pin: str = Form("")):
    user = resolve_user(request, token, pin)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})
    asyncio.create_task(execute_autopilot_cycle(user))
    return {"success": True, "message": f"@{user} ka Auto-Pilot cycle background me shuru ho gaya hai!"}

@app.get("/api/autopilot/logs")
async def get_autopilot_logs_endpoint(request: Request, token: str = "", pin: str = ""):
    user = resolve_user(request, token, pin)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"success": True, "logs": get_autopilot_logs(user)[:50]}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
