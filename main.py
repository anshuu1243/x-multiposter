import os
import json
import asyncio
import tempfile
import aiofiles
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from twikit import Client

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
TEMPLATES_DIR = BASE_DIR / "templates"

DEFAULT_PIN = os.getenv("DASHBOARD_PIN", "1234")

app = FastAPI(title="X Multi-Account Shop Poster")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def split_text_into_chunks(text: str, max_len: int = 265) -> List[str]:
    """Splits long text into auto-threaded parts if it exceeds Twitter's 280 char limit."""
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
    # Add thread markers like (1/3), (2/3)
    return [f"{chunk} ({i+1}/{total})" for i, chunk in enumerate(chunks)]

def get_saved_accounts() -> List[dict]:
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_accounts(accounts: List[dict]):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "default_pin": DEFAULT_PIN
    })

@app.get("/api/accounts")
async def list_accounts(pin: str = ""):
    if pin != DEFAULT_PIN:
        raise HTTPException(status_code=401, detail="Galat PIN hai!")
    return {"accounts": get_saved_accounts()}

@app.post("/api/accounts/add")
async def add_account(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    pin: str = Form(...)
):
    if pin != DEFAULT_PIN:
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
    if pin != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    username_clean = username.strip().lstrip("@")
    cookie_path = COOKIES_DIR / f"{username_clean}.json"
    if cookie_path.exists():
        cookie_path.unlink()

    accounts = get_saved_accounts()
    accounts = [acc for acc in accounts if acc.get("username") != username_clean]
    save_accounts(accounts)

    return {"success": True, "message": f"@{username_clean} ko remove kar diya gaya."}

@app.post("/api/post")
async def create_multi_post(
    text: str = Form(...),
    accounts: str = Form(...),
    pin: str = Form(...),
    image: Optional[UploadFile] = File(None)
):
    if pin != DEFAULT_PIN:
        return JSONResponse(status_code=401, content={"success": False, "message": "Galat PIN!"})

    target_usernames = [u.strip().lstrip("@") for u in accounts.split(",") if u.strip()]
    if not target_usernames:
        return JSONResponse(status_code=400, content={"success": False, "message": "Koi account select nahi kiya!"})

    # Prepare chunks if text is longer than 280 chars
    chunks = split_text_into_chunks(text)

    saved_image_path = None
    if image and image.filename:
        suffix = Path(image.filename).suffix or ".jpg"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        async with aiofiles.open(temp_file.name, "wb") as f:
            content = await image.read()
            await f.write(content)
        saved_image_path = temp_file.name

    results = []

    try:
        for username in target_usernames:
            cookie_path = COOKIES_DIR / f"{username}.json"
            if not cookie_path.exists():
                results.append({"username": username, "status": "error", "message": "Cookies nahi mili! Reconnect karein."})
                continue

            try:
                client = Client(language="en-US")
                client.load_cookies(str(cookie_path))

                # Handle image upload for the first tweet
                media_ids = []
                if saved_image_path:
                    media_id = await client.upload_media(saved_image_path)
                    media_ids.append(media_id)

                # Post first tweet (with media if present)
                if media_ids:
                    tweet = await client.create_tweet(text=chunks[0], media_ids=media_ids)
                else:
                    tweet = await client.create_tweet(text=chunks[0])

                # If long post (>280 chars), automatically post remaining as thread replies!
                last_tweet_id = tweet.id if hasattr(tweet, 'id') else None
                for reply_chunk in chunks[1:]:
                    await asyncio.sleep(1.5)
                    reply_tweet = await client.create_tweet(text=reply_chunk, reply_to=last_tweet_id)
                    last_tweet_id = reply_tweet.id if hasattr(reply_tweet, 'id') else last_tweet_id

                status_msg = f"Posted! ({len(chunks)} tweets thread)" if len(chunks) > 1 else "Posted!"
                results.append({"username": username, "status": "success", "message": status_msg})
                await asyncio.sleep(2)
            except Exception as e:
                results.append({"username": username, "status": "error", "message": str(e)})

    finally:
        if saved_image_path and os.path.exists(saved_image_path):
            try:
                os.remove(saved_image_path)
            except Exception:
                pass

    return {"success": True, "results": results}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
