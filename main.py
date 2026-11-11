import os
import json
import asyncio
import tempfile
import aiofiles
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from twikit import Client

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)
ACCOUNTS_FILE = BASE_DIR / "accounts.json"

# Secret PIN set to 1801
DEFAULT_PIN = os.getenv("DASHBOARD_PIN", "1801")

app = FastAPI(title="Mr-KING Multi-Poster")

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
async def home_page():
    html = get_index_html()
    return HTMLResponse(content=html)

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

# Single Account Post Endpoint (Real-Time Live Progress & Exact Error Tracking)
@app.post("/api/post-single")
async def post_single(
    username: str = Form(...),
    text: str = Form(...),
    pin: str = Form(...),
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

        msg = f"Successfully Posted! ({len(chunks)} tweets thread)" if len(chunks) > 1 else "Successfully Posted!"
        return {"success": True, "message": msg}

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
