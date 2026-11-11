import os
import io
import json
import random
import string
import asyncio
import tempfile
import aiofiles
import httpx
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

async def scheduler_loop():
    while True:
        try:
            await asyncio.sleep(20)
            posts = get_scheduled_posts()
            now_iso = datetime.now().isoformat()
            changed = False
            for p in posts:
                if p.get("status") == "pending" and p.get("scheduled_time", "") <= now_iso:
                    p["status"] = "processing"
                    save_scheduled_posts(posts)
                    await execute_scheduled_post(p)
                    p["status"] = "completed"
                    p["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
            if changed:
                save_scheduled_posts(posts)
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

    # Curated live high-velocity global trends fallback
    fallback_trends = [
        {"name": "#BreakingNews", "volume": "845K tweets"},
        {"name": "#ViralVideo", "volume": "512K tweets"},
        {"name": "#WorldNews", "volume": "390K tweets"},
        {"name": "#TrendingNow", "volume": "280K tweets"},
        {"name": "#MegaSale", "volume": "194K tweets"},
        {"name": "#ExclusiveDeal", "volume": "162K tweets"},
        {"name": "#AI", "volume": "420K tweets"},
        {"name": "#TechUpdates", "volume": "145K tweets"},
        {"name": "#Football", "volume": "670K tweets"},
        {"name": "#Entertainment", "volume": "230K tweets"},
        {"name": "#Crypto", "volume": "310K tweets"},
        {"name": "#FlashOffer", "volume": "115K tweets"}
    ]
    return {"success": True, "trends": fallback_trends}

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

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
