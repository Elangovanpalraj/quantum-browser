"""
Quantum Browser - Hosted Website Version (with Login/Signup)
------------------------------------------------------------------
Idha Render.com (or edhavadhu free Python hosting) la deploy pannina,
laptop/mobile edhu venaalum browser-la oru URL open pannitu login
pannitu use pannalam - install onnum thevai illa.

Note: Idhu website-ah irukkurathala, Google/YouTube polaadha external
sites SAME page-kulla embed aaga mudiyaadhu (browser security) - search
pannina result PUDHU TAB-la open aagum. Quantum AI chat mattum, home
page mattum SAME site-kulla work aagum.

LOCAL TESTING:
    pip install flask groq pymupdf pandas pillow werkzeug
    setx GROQ_API_KEY "your-key"
    python quantum_web_app.py
    -> browser-la http://127.0.0.1:5000 open pannunga

DEPLOY (Render.com free tier - recommended):
    1. Idha ellam (quantum_web_app.py, quantum_web_home.html,
       requirements.txt) oru GitHub repo-la push pannunga.
    2. render.com la sign up pannunga (free).
    3. "New +" -> "Web Service" -> connect your GitHub repo.
    4. Build command:  pip install -r requirements.txt
       Start command:  gunicorn quantum_web_app:app --bind 0.0.0.0:$PORT
    5. Environment tab la add pannunga:
         GROQ_API_KEY   = your groq key
         SECRET_KEY     = edhavadhu random secret string
    6. Deploy click pannunga. Konjam nimisham kaathirunga.
    7. Render kudukkura URL (e.g. https://quantum-xyz.onrender.com)
       ah edhu venaalum browser-la open pannunga - login/signup pannitu
       use pannalam!

    NOTE: Free tier apps 15 nimisham inactivity-ku aprm "sleep" aagum;
    next request first-a konjam slow-a (30-60 sec) start aagum - idhu
    free hosting oda normal trade-off.
"""

import os
import sqlite3
import secrets
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template_string, redirect,
    url_for, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

try:
    import pandas as pd
except ImportError:
    pd = None

import io
import base64
import re as _re

# ---------------------------------------------------------------------------
# SAFETY NET: Copy-pasting code through GitHub's web editor can sometimes
# corrupt a multi-byte emoji into a "lone surrogate" character, which crashes
# Flask with UnicodeEncodeError when it tries to send the page. This strips
# any such bad characters out of EVERY page before it is sent, so a stray
# corrupted character can never crash the site again.
# ---------------------------------------------------------------------------
_SURROGATE_RE = _re.compile("[\ud800-\udfff]")


def safe_render(template_string, **kwargs):
    from flask import render_template_string as _rts
    out = _rts(template_string, **kwargs)
    return _SURROGATE_RE.sub("", out)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "quantum_users.db")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# 2026-ல Groq recommend pandra powerful, latest models (free tier-lyum kidaikkum):
#   - openai/gpt-oss-120b : best general reasoning/chat model on Groq right now
#   - meta-llama/llama-4-scout-17b-16e-instruct : vision (image understanding) model
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
groq_client = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None
APP_VERSION = "1.1"  # ivvalavu update aagum bothum idha increase pannunga
GITHUB_REPO = "Elangovanpalraj/quantum-browser"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
MIME_MAP = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
}

SYSTEM_PROMPT = (
    "You are a multilingual assistant called Quantum AI, built into the "
    "Quantum Browser.\n"
    "Detect the language automatically and reply in the SAME language. "
    "Mixed languages (Tanglish/Hinglish) should get a reply in the same "
    "mixed style. Be honest, clear, and reasonably concise. When showing "
    "code, wrap it in triple backticks with the language name."
)

# Each "tool" in the sidebar is really the SAME chat engine with a
# different personality/focus layered on top of the base SYSTEM_PROMPT.
MODE_PROMPTS = {
    "general": "",
    "coding": (
        "\nMode: Coding AI. Focus on writing clean, correct, well-commented "
        "code. Prefer giving working code over long explanations. Always "
        "use triple-backtick fenced code blocks with the language name so "
        "the code renders in the side panel."
    ),
    "resume": (
        "\nMode: Resume AI. Help the user write or improve resumes, cover "
        "letters, and LinkedIn summaries. Ask for missing details (role, "
        "experience, skills) if not given, then produce a clean, "
        "well-formatted resume section in Markdown."
    ),
    "roleplay": (
        "\nMode: Roleplay AI. Stay in character for whatever fictional "
        "persona or scenario the user sets up, keep replies immersive and "
        "creative, while still being safe and appropriate."
    ),
    "image": (
        "\nMode: Image AI. The user may attach a photo. Describe, analyze, "
        "or answer questions about the attached image in detail. Quantum "
        "AI cannot generate new images yet, only understand uploaded ones."
    ),
}


def build_system_prompt(mode):
    return SYSTEM_PROMPT + MODE_PROMPTS.get(mode, "")


# ---------------------------------------------------------------- DB
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS browse_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            visited_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    db.close()


init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- Auth pages
AUTH_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Quantum - {{ mode }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root{ --bg:#0b0d10; --panel:#15181c; --panel2:#1c2025; --border:#2a2e34;
    --text:#eceef1; --dim:#8b9099; --violet:#7c6cff; --cyan:#5ee8d5; }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{ background:radial-gradient(1200px 700px at 50% -10%, #171a20 0%, var(--bg) 55%);
    color:var(--text); font-family:'Segoe UI',sans-serif;
    min-height:100vh; display:flex; align-items:center; justify-content:center; }
  .card{ width:100%; max-width:380px; background:var(--panel); border:1px solid var(--border);
    border-radius:16px; padding:36px 32px; }
  h1{ font-size:22px; margin-bottom:6px; background:linear-gradient(135deg,#d8dbe0,var(--violet),var(--cyan));
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  p.sub{ color:var(--dim); font-size:13px; margin-bottom:24px; }
  label{ font-size:12.5px; color:var(--dim); display:block; margin:14px 0 6px; }
  input{ width:100%; background:var(--panel2); color:var(--text); border:1px solid var(--border);
    border-radius:10px; padding:11px 14px; font-size:14px; outline:none; }
  input:focus{ border-color:var(--violet); }
  button{ width:100%; margin-top:22px; background:linear-gradient(135deg,var(--violet),var(--cyan));
    color:#0b0d10; border:none; border-radius:10px; padding:12px; font-weight:700; cursor:pointer; font-size:14px; }
  .error{ background:rgba(255,90,90,.12); border:1px solid rgba(255,90,90,.35); color:#ff9b9b;
    padding:10px 14px; border-radius:10px; font-size:12.5px; margin-bottom:14px; }
  .switch{ text-align:center; margin-top:20px; font-size:13px; color:var(--dim); }
  .switch a{ color:var(--cyan); text-decoration:none; font-weight:600; }
</style></head>
<body>
  <div class="card">
    <h1>Quantum</h1>
    <p class="sub">{{ 'Create your account' if mode == 'Sign Up' else 'Welcome back' }}</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="POST">
      <label>Email</label>
      <input type="email" name="email" required autofocus>
      <label>Password</label>
      <input type="password" name="password" required minlength="6">
      <button type="submit">{{ mode }}</button>
    </form>
    {% if mode == 'Sign Up' %}
      <div class="switch">Already have an account? <a href="/login">Log in</a></div>
    {% else %}
      <div class="switch">New here? <a href="/signup">Create an account</a></div>
    {% endif %}
    <div class="switch" style="margin-top:10px;"><a href="/download">[Download] Download Desktop App</a></div>
  </div>
</body></html>
"""


GITHUB_RELEASE_URL = "https://github.com/Elangovanpalraj/quantum-browser/releases/download/v1.0/QuantumBrowser.exe"

DOWNLOAD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Download Quantum Browser</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root{ --bg:#0b0d10; --panel:#15181c; --panel2:#1c2025; --border:#2a2e34;
    --text:#eceef1; --dim:#8b9099; --violet:#7c6cff; --cyan:#5ee8d5; }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{ background:radial-gradient(1200px 700px at 50% -10%, #171a20 0%, var(--bg) 55%);
    color:var(--text); font-family:'Segoe UI',sans-serif; min-height:100vh;
    display:flex; flex-direction:column; align-items:center; padding:60px 24px; }
  .logo{ width:76px; height:76px; border-radius:20px; background:#0b0d10;
    display:flex; align-items:center; justify-content:center; margin-bottom:20px;
    border:1px solid var(--border); }
  h1{ font-size:32px; margin-bottom:8px;
    background:linear-gradient(135deg,#d8dbe0,var(--violet),var(--cyan));
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  p.sub{ color:var(--dim); font-size:15px; margin-bottom:36px; text-align:center; max-width:480px; }
  .download-card{ background:var(--panel); border:1px solid var(--border); border-radius:16px;
    padding:32px; width:100%; max-width:420px; text-align:center; }
  .download-btn{ display:inline-block; width:100%; background:linear-gradient(135deg,var(--violet),var(--cyan));
    color:#0b0d10; text-decoration:none; font-weight:700; padding:16px; border-radius:12px;
    font-size:16px; margin-top:10px; }
  .meta{ color:var(--dim); font-size:12.5px; margin-top:14px; }
  .steps{ margin-top:40px; max-width:480px; width:100%; }
  .steps h2{ font-size:15px; color:var(--dim); margin-bottom:14px; font-weight:600;
    text-transform:uppercase; letter-spacing:.05em; }
  .step{ display:flex; gap:12px; padding:12px 0; border-top:1px solid var(--border); font-size:14px; }
  .step .n{ width:24px; height:24px; border-radius:50%; background:var(--panel2);
    display:flex; align-items:center; justify-content:center; font-size:12px;
    color:var(--cyan); flex-shrink:0; font-weight:700; }
  a.back{ color:var(--dim); text-decoration:none; font-size:13px; margin-top:30px; }
</style></head>
<body>
  <div class="logo">
    <svg width="44" height="44" viewBox="0 0 100 100">
      <defs><linearGradient id="g1" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f4f5f7"/><stop offset="100%" stop-color="#8b909a"/></linearGradient>
      <linearGradient id="g2" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#7c6cff"/><stop offset="100%" stop-color="#5ee8d5"/></linearGradient></defs>
      <circle cx="48" cy="48" r="30" fill="none" stroke="url(#g1)" stroke-width="13"/>
      <line x1="66" y1="66" x2="80" y2="80" stroke="url(#g1)" stroke-width="10" stroke-linecap="round"/>
      <path d="M10 62 Q48 78 92 50" fill="none" stroke="url(#g2)" stroke-width="4.5" stroke-linecap="round"/>
    </svg>
  </div>
  <h1>Download Quantum Browser</h1>
  <p class="sub">Real browsing engine, built-in Quantum AI chat, and a fast home page - free for Windows.</p>

  <div class="download-card">
    <div style="font-weight:700; font-size:18px;">Quantum Browser for Windows</div>
    <div class="meta">Version 1.0 &middot; Windows 10/11 (64-bit)</div>
    <a class="download-btn" href="{{ download_url }}">[Download] Download for Windows</a>
    <div class="meta">~110 MB &middot; .exe file</div>
  </div>

  <div class="steps">
    <h2>How to install</h2>
    <div class="step"><span class="n">1</span> Click "Download for Windows" above.</div>
    <div class="step"><span class="n">2</span> Open the downloaded QuantumBrowser.exe file.</div>
    <div class="step"><span class="n">3</span> Windows may show a SmartScreen warning since this is a new app - click "More info" &rarr; "Run anyway".</div>
    <div class="step"><span class="n">4</span> Quantum Browser opens directly - no installer wizard needed.</div>
    <div class="step"><span class="n">5</span> (Optional) Drag the .exe to your Desktop for quick access anytime.</div>
  </div>

  <a class="back" href="/">&larr; Back to Quantum</a>
</body></html>
"""


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or len(password) < 6:
            return safe_render(AUTH_HTML, mode="Sign Up",
                                           error="Valid email + 6-char password venum.")
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            return safe_render(AUTH_HTML, mode="Sign Up",
                                           error="Indha email already registered. Login pannunga.")
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                   (email, generate_password_hash(password)))
        db.commit()
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        session["user_id"] = user["id"]
        session["email"] = email
        return redirect(url_for("home"))
    return safe_render(AUTH_HTML, mode="Sign Up", error=None)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return safe_render(AUTH_HTML, mode="Login",
                                           error="Email/password thappu irukku.")
        session["user_id"] = user["id"]
        session["email"] = email
        return redirect(url_for("home"))
    return safe_render(AUTH_HTML, mode="Login", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- Download page
GITHUB_RELEASE_URL = os.environ.get(
    "DOWNLOAD_URL",
    "https://github.com/Elangovanpalraj/quantum-browser/releases/latest"
)

DOWNLOAD_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Download Quantum Browser</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root{ --bg:#0b0d10; --panel:#15181c; --panel2:#1c2025; --border:#2a2e34;
    --text:#eceef1; --dim:#8b9099; --violet:#7c6cff; --cyan:#5ee8d5; }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{ background:radial-gradient(1200px 700px at 50% -10%, #171a20 0%, var(--bg) 55%);
    color:var(--text); font-family:'Segoe UI',sans-serif; min-height:100vh;
    display:flex; flex-direction:column; align-items:center; padding:60px 20px; }
  .logo{ width:64px; height:64px; border-radius:16px; background:#0b0d10;
    display:flex; align-items:center; justify-content:center; margin-bottom:18px;
    border:1px solid var(--border); }
  h1{ font-size:32px; margin-bottom:8px;
    background:linear-gradient(135deg,#d8dbe0,var(--violet),var(--cyan));
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  p.sub{ color:var(--dim); font-size:14px; margin-bottom:36px; text-align:center; max-width:480px; }
  .download-btn{ display:inline-flex; align-items:center; gap:10px;
    background:linear-gradient(135deg,var(--violet),var(--cyan)); color:#0b0d10;
    text-decoration:none; font-weight:700; font-size:16px; padding:16px 32px;
    border-radius:14px; margin-bottom:48px; }
  .steps{ width:100%; max-width:520px; background:var(--panel); border:1px solid var(--border);
    border-radius:16px; padding:28px 32px; }
  .steps h2{ font-size:15px; margin-bottom:18px; color:var(--dim); text-transform:uppercase;
    letter-spacing:.06em; }
  .step{ display:flex; gap:14px; margin-bottom:18px; }
  .step:last-child{ margin-bottom:0; }
  .num{ width:26px; height:26px; border-radius:50%; background:var(--panel2);
    border:1px solid var(--border); display:flex; align-items:center; justify-content:center;
    font-size:12.5px; font-weight:700; color:var(--cyan); flex-shrink:0; }
  .step-text{ font-size:13.5px; line-height:1.6; color:var(--text); }
  .step-text b{ color:var(--cyan); }
  .back{ margin-top:32px; color:var(--dim); font-size:13px; text-decoration:none; }
  .back:hover{ color:var(--text); }
  .note{ margin-top:20px; font-size:12px; color:var(--dim); text-align:center; max-width:480px; }
</style></head>
<body>
  <div class="logo">
    <svg width="34" height="34" viewBox="0 0 100 100">
      <defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#f4f5f7"/><stop offset="100%" stop-color="#8b909a"/>
      </linearGradient></defs>
      <circle cx="48" cy="48" r="30" fill="none" stroke="url(#g)" stroke-width="13"/>
      <line x1="66" y1="66" x2="80" y2="80" stroke="url(#g)" stroke-width="10" stroke-linecap="round"/>
    </svg>
  </div>
  <h1>Quantum Browser</h1>
  <p class="sub">Real browsing engine + built-in Quantum AI - free desktop app for Windows.</p>

  <a class="download-btn" href="{{ download_url }}">[Download] Download for Windows</a>

  <div class="steps">
    <h2>Install Steps</h2>
    <div class="step"><div class="num">1</div>
      <div class="step-text">Above button click பண்ணி <b>QuantumBrowser.exe</b> download பண்ணு.</div></div>
    <div class="step"><div class="num">2</div>
      <div class="step-text">Downloads folder-ல இருந்து <b>Desktop</b>-க்கு copy பண்ணு.</div></div>
    <div class="step"><div class="num">3</div>
      <div class="step-text">Double-click பண்ணு open பண்ணு - "Windows protected your PC" வந்தா,
        <b>"More info" -> "Run anyway"</b> click பண்ணு (unsigned free app-க்கு இது normal).</div></div>
    <div class="step"><div class="num">4</div>
      <div class="step-text">Quantum Browser window open ஆகும் - Google/YouTube search பண்ணு,
        Quantum AI chat பண்ணு பாரு!</div></div>
  </div>

  <p class="note">இது ஒரு free, personal project. Antivirus warning வந்தா, unsigned .exe file-க்கு
    common-ஆ இது நடக்கும் - கவலைப்பட வேண்டாம்.</p>

  <a class="back" href="/">&larr; Back to Quantum</a>
</body></html>
"""


@app.route("/download")
def download():
    return safe_render(DOWNLOAD_HTML, download_url=GITHUB_RELEASE_URL)


# ---------------------------------------------------------------- Auto-update check
@app.route("/api/check_update")
def check_update():
    """Desktop .exe idha hit panni, pudhu version irukka nu check pannikkalam."""
    import urllib.request, json as _json
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        latest_tag = (data.get("tag_name") or "").lstrip("v")
        download_url = data.get("html_url", GITHUB_RELEASE_URL)
        return jsonify({
            "current_version": APP_VERSION,
            "latest_version": latest_tag,
            "update_available": latest_tag != "" and latest_tag != APP_VERSION,
            "download_url": download_url,
        })
    except Exception as e:
        return jsonify({"current_version": APP_VERSION, "update_available": False, "error": str(e)})


# ---------------------------------------------------------------- Bookmarks / History / Saved Searches
@app.route("/api/bookmarks", methods=["GET", "POST"])
@login_required
def bookmarks():
    uid = session["user_id"]
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        title = (data.get("title") or "Untitled").strip()
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "error": "url required"}), 400
        db.execute("INSERT INTO bookmarks (user_id, title, url) VALUES (?, ?, ?)", (uid, title, url))
        db.commit()
        return jsonify({"success": True})
    rows = db.execute(
        "SELECT id, title, url, created_at FROM bookmarks WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/bookmarks/<int:bid>", methods=["DELETE"])
@login_required
def delete_bookmark(bid):
    db = get_db()
    db.execute("DELETE FROM bookmarks WHERE id=? AND user_id=?", (bid, session["user_id"]))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/history", methods=["GET", "POST"])
@login_required
def history():
    uid = session["user_id"]
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        title = (data.get("title") or "Untitled").strip()
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "error": "url required"}), 400
        db.execute("INSERT INTO browse_history (user_id, title, url) VALUES (?, ?, ?)", (uid, title, url))
        db.commit()
        return jsonify({"success": True})
    rows = db.execute(
        "SELECT id, title, url, visited_at FROM browse_history WHERE user_id=? ORDER BY id DESC LIMIT 100",
        (uid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/clear", methods=["POST"])
@login_required
def clear_history():
    db = get_db()
    db.execute("DELETE FROM browse_history WHERE user_id=?", (session["user_id"],))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/saved_searches", methods=["GET", "POST"])
@login_required
def saved_searches():
    uid = session["user_id"]
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"success": False, "error": "query required"}), 400
        db.execute("INSERT INTO saved_searches (user_id, query) VALUES (?, ?)", (uid, query))
        db.commit()
        return jsonify({"success": True})
    rows = db.execute(
        "SELECT id, query, created_at FROM saved_searches WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


LIBRARY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Quantum Library</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root{ --bg:#f5f6f8; --panel:#ffffff; --panel2:#eef0f3; --border:#dde1e6;
    --text:#16181c; --dim:#6b7078; --violet:#6a5cff; --cyan:#0fb8a0; }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{ background:radial-gradient(1200px 700px at 50% -10%, #ffffff 0%, var(--bg) 55%);
    color:var(--text); font-family:'Segoe UI',sans-serif; min-height:100vh; padding:0 0 60px; }
  header{ padding:18px 28px; border-bottom:1px solid var(--border); display:flex;
    align-items:center; gap:10px; background:#ffffff; position:sticky; top:0; z-index:5; }
  header .dot{ width:10px;height:10px;border-radius:50%; background:linear-gradient(135deg,var(--cyan),var(--violet)); }
  header h1{ font-size:16px; font-weight:600; }
  header a{ margin-left:auto; color:var(--dim); font-size:13px; text-decoration:none; }
  header a:hover{ color:var(--text); }
  .tabs{ display:flex; gap:8px; padding:20px 28px 0; }
  .tab{ padding:9px 18px; border-radius:999px; background:var(--panel2); border:1px solid var(--border);
    font-size:13.5px; cursor:pointer; color:var(--dim); }
  .tab.active{ background:linear-gradient(135deg,var(--violet),var(--cyan)); color:#f5f6f8; font-weight:700; border:none; }
  .grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; padding:22px 28px; }
  .card{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:18px;
    opacity:0; transform:translateY(14px); animation:rise .45s ease forwards; position:relative; }
  .card:nth-child(n){ animation-delay:calc(var(--i,0) * 0.04s); }
  @keyframes rise{ to{ opacity:1; transform:translateY(0); } }
  .card:hover{ border-color:var(--violet); transform:translateY(-3px); transition:.2s; }
  .card .t{ font-weight:600; font-size:14.5px; margin-bottom:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .card .u{ color:var(--cyan); font-size:12px; text-decoration:none; word-break:break-all; }
  .card .d{ color:var(--dim); font-size:11px; margin-top:10px; }
  .card .x{ position:absolute; top:10px; right:12px; color:var(--dim); cursor:pointer; font-weight:700; }
  .empty{ color:var(--dim); padding:60px 28px; text-align:center; font-size:14px; }
</style></head>
<body>
  <header><span class="dot"></span><h1>Quantum Library</h1><a href="/">&larr; Back to Quantum</a></header>
  <div class="tabs">
    <div class="tab active" data-tab="bookmarks">[Bookmarks] Bookmarks</div>
    <div class="tab" data-tab="history">[History] History</div>
    <div class="tab" data-tab="searches">[Search] Saved Searches</div>
  </div>
  <div class="grid" id="grid"></div>
<script>
  const grid=document.getElementById('grid'), tabs=document.querySelectorAll('.tab');
  let current='bookmarks';
  async function load(tab){
    current=tab;
    tabs.forEach(t=>t.classList.toggle('active', t.dataset.tab===tab));
    const endpoint = tab==='bookmarks'?'/api/bookmarks': tab==='history'?'/api/history':'/api/saved_searches';
    const res=await fetch(endpoint); const items=await res.json();
    grid.innerHTML='';
    if(!items.length){ grid.innerHTML='<div class="empty">Innum onnum illa இங்க.</div>'; return; }
    items.forEach((it,i)=>{
      const c=document.createElement('div'); c.className='card'; c.style.setProperty('--i',i);
      if(tab==='searches'){
        c.innerHTML = '<div class="t">'+it.query+'</div><div class="d">'+it.created_at+'</div>'+
          '<span class="x" data-id="'+it.id+'">\u2715</span>';
      } else {
        const dateField = tab==='bookmarks' ? it.created_at : it.visited_at;
        c.innerHTML = '<div class="t">'+it.title+'</div><a class="u" href="'+it.url+'" target="_blank">'+it.url+'</a>'+
          '<div class="d">'+dateField+'</div><span class="x" data-id="'+it.id+'">\u2715</span>';
      }
      grid.appendChild(c);
    });
    grid.querySelectorAll('.x').forEach(x=>x.addEventListener('click', async ()=>{
      if(tab==='bookmarks') await fetch('/api/bookmarks/'+x.dataset.id, {method:'DELETE'});
      load(tab);
    }));
  }
  tabs.forEach(t=>t.addEventListener('click', ()=>load(t.dataset.tab)));
  load('bookmarks');
</script>
</body></html>
"""


@app.route("/library")
@login_required
def library():
    return safe_render(LIBRARY_HTML)


# ---------------------------------------------------------------- Home page
def _load_home_html():
    path = os.path.join(BASE_DIR, "quantum_web_home.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


HOME_HTML = _load_home_html()


@app.route("/")
@login_required
def home():
    return safe_render(HOME_HTML, user_email=session.get("email", ""))


# ---------------------------------------------------------------- Quantum AI page
AI_PAGE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Quantum AI</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root{ --bg:#f5f6f8; --panel:#ffffff; --panel2:#eef0f3; --border:#dde1e6;
    --text:#16181c; --dim:#6b7078; --violet:#6a5cff; --cyan:#0fb8a0; }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{ background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif;
    height:100vh; display:flex; flex-direction:column; overflow:hidden; }
  header{ padding:16px 24px; border-bottom:1px solid var(--border); display:flex;
    align-items:center; gap:10px; background:linear-gradient(180deg,#ffffff,#f5f6f8); }
  header .dot{ width:10px;height:10px;border-radius:50%;
    background:linear-gradient(135deg,var(--cyan),var(--violet)); }
  header h1{ font-size:16px; font-weight:600; }
  header a{ margin-left:auto; color:var(--dim); font-size:13px; text-decoration:none; }
  header a:hover{ color:var(--text); }
  .mode-tabs{ display:flex; gap:8px; padding:12px 24px; border-bottom:1px solid var(--border);
    background:var(--panel); overflow-x:auto; }
  .mode-tab{ padding:8px 16px; border-radius:999px; background:var(--panel2); border:1px solid var(--border);
    font-size:12.5px; color:var(--dim); cursor:pointer; white-space:nowrap; flex-shrink:0; }
  .mode-tab.active{ background:linear-gradient(135deg,var(--violet),var(--cyan)); color:#fff; font-weight:700; border:none; }
  .workspace{ flex:1; display:flex; min-height:0; }
  .chat-col{ flex:1; display:flex; flex-direction:column; min-width:0; transition:flex .25s ease; }
  #chat{ flex:1; overflow-y:auto; padding:24px; display:flex; flex-direction:column; gap:14px; }
  .bubble{ max-width:70%; padding:12px 16px; border-radius:14px; font-size:14px;
    line-height:1.5; white-space:pre-wrap; }
  .user{ align-self:flex-end; background:var(--violet); color:#fff; }
  .bot{ align-self:flex-start; background:var(--panel2); border:1px solid var(--border); }
  .bot.loading{ color:var(--dim); font-style:italic; }
  .bubble img{ max-width:220px; border-radius:10px; display:block; margin-top:6px; }
  #attach-chip{ display:none; align-items:center; gap:8px; margin:0 24px 10px 24px;
    padding:8px 12px; background:var(--panel2); border:1px solid var(--border);
    border-radius:10px; font-size:12.5px; color:var(--dim); }
  #attach-chip.show{ display:flex; }
  #attach-chip img{ width:28px; height:28px; border-radius:6px; object-fit:cover; }
  #attach-chip .x{ margin-left:auto; cursor:pointer; color:var(--dim); font-weight:700; padding:0 6px; }
  form{ display:flex; gap:8px; padding:16px 24px; border-top:1px solid var(--border);
    background:var(--panel); align-items:center; position:relative; }
  input[type=text]{ flex:1; background:var(--panel2); color:var(--text); border:1px solid var(--border);
    border-radius:999px; padding:12px 18px; font-size:14px; outline:none; }
  input[type=text]:focus{ border-color:var(--violet); }
  button{ background:linear-gradient(135deg,var(--violet),var(--cyan)); color:#f5f6f8; border:none;
    border-radius:999px; padding:0 22px; font-weight:600; cursor:pointer; font-size:14px; flex-shrink:0; }
  button:disabled{ opacity:.5; cursor:default; }
  .icon-btn{ width:40px; height:40px; border-radius:50%; background:var(--panel2);
    border:1px solid var(--border); color:#16181c; font-size:18px; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0; padding:0; position:relative; }
  .icon-btn:hover{ background:#e2e5ea; }
  #plusMenu{ display:none; position:absolute; bottom:56px; left:24px; background:var(--panel2);
    border:1px solid var(--border); border-radius:12px; padding:6px; min-width:200px;
    box-shadow:0 12px 30px -8px rgba(0,0,0,.6); }
  #plusMenu.show{ display:block; }
  #plusMenu .item{ display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:8px;
    cursor:pointer; font-size:13.5px; color:var(--text); }
  #plusMenu .item:hover{ background:var(--panel); }
  .code-block{ margin-top:8px; background:#f5f6f8; border:1px solid var(--border); border-radius:10px; overflow:hidden; }
  .code-head{ display:flex; align-items:center; justify-content:space-between; padding:8px 12px;
    background:#eef0f3; font-size:11.5px; color:var(--dim); }
  .code-head .lang{ text-transform:uppercase; letter-spacing:.04em; font-weight:700; color:var(--cyan); }
  .code-head .actions{ display:flex; gap:8px; }
  .code-head button{ background:var(--panel2); border:1px solid var(--border); color:var(--text);
    font-size:11px; padding:4px 10px; border-radius:6px; cursor:pointer; }
  .code-head button:hover{ background:#e2e5ea; }
  .code-block pre{ margin:0; padding:14px; overflow-x:auto; font-family:Consolas,Menlo,monospace;
    font-size:12.5px; line-height:1.5; color:#16181c; white-space:pre; }
  .open-panel-btn{ background:var(--panel2); color:var(--cyan); border:1px solid var(--border);
    font-size:11.5px; padding:4px 10px; border-radius:6px; cursor:pointer; margin-left:8px; }

  /* ---- Right-side code / artifact panel ---- */
  #artifactPanel{ width:0; overflow:hidden; border-left:1px solid var(--border);
    background:#f5f6f8; display:flex; flex-direction:column; transition:width .25s ease; flex-shrink:0; }
  #artifactPanel.open{ width:46%; min-width:340px; }
  .art-head{ display:flex; align-items:center; gap:10px; padding:14px 18px; border-bottom:1px solid var(--border);
    background:#ffffff; }
  .art-head .ai-title{ font-size:13.5px; font-weight:700; }
  .art-head .ai-lang{ font-size:11px; color:var(--cyan); background:var(--panel2); padding:3px 9px;
    border-radius:999px; border:1px solid var(--border); }
  .art-head .close-btn{ margin-left:auto; background:none; border:none; color:var(--dim); font-size:18px;
    cursor:pointer; padding:0 4px; }
  .art-tabs{ display:flex; gap:6px; padding:8px 14px 0; }
  .art-tab{ padding:6px 12px; border-radius:8px 8px 0 0; font-size:11.5px; color:var(--dim);
    background:var(--panel2); cursor:pointer; border:1px solid var(--border); border-bottom:none; }
  .art-tab.active{ color:var(--cyan); background:#f5f6f8; }
  .art-body{ flex:1; overflow:auto; padding:16px 18px; font-family:Consolas,Menlo,monospace;
    font-size:12.5px; line-height:1.6; color:#16181c; white-space:pre; }
  .art-footer{ padding:12px 18px; border-top:1px solid var(--border); display:flex; gap:10px; }
  .art-footer button{ flex:1; font-size:13px; }
  .art-footer .secondary{ background:var(--panel2); color:var(--text); border:1px solid var(--border); }
</style></head>
<body>
  <header><span class="dot"></span><h1>Quantum AI</h1><a href="/">&larr; Back to Quantum</a></header>
  <div class="mode-tabs" id="modeTabs">
    <div class="mode-tab active" data-mode="general">Quantum AI</div>
    <div class="mode-tab" data-mode="coding">Coding AI</div>
    <div class="mode-tab" data-mode="resume">Resume AI</div>
    <div class="mode-tab" data-mode="roleplay">Roleplay AI</div>
    <div class="mode-tab" data-mode="image">Image AI</div>
  </div>
  <div class="workspace">
    <div class="chat-col">
      <div id="chat"></div>
      <div id="attach-chip"><span>[Attach]</span><img id="chip-img" style="display:none"><span id="chip-name"></span>
        <span class="x" id="chip-remove">x</span></div>
      <form id="f">
        <button type="button" class="icon-btn" id="plusBtn" title="Add">+</button>
        <div id="plusMenu"><div class="item" id="menuAddFile">[Attach] &nbsp;Add files or photos</div></div>
        <input type="file" id="fileInput" style="display:none" accept=".png,.jpg,.jpeg,.gif,.bmp,.webp,.pdf,.csv,.txt,.md,.docx,.pptx,.py,.js,.ts,.java,.c,.cpp,.cs,.go,.rb,.php,.html,.css,.json,.xml,.sql,.sh">
        <input type="text" id="msg" placeholder="Ask Quantum AI anything..." autocomplete="off">
        <button type="submit" id="sendBtn">Send</button>
      </form>
    </div>
    <div id="artifactPanel">
      <div class="art-head">
        <span class="ai-title" id="artTitle">quantum_code</span>
        <span class="ai-lang" id="artLang">TXT</span>
        <button class="close-btn" id="artClose">x</button>
      </div>
      <div class="art-tabs" id="artTabs"></div>
      <div class="art-body" id="artBody"></div>
      <div class="art-footer">
        <button class="secondary" id="artCopy">[Copy] Copy</button>
        <button id="artDownload">[Download] Download File</button>
      </div>
    </div>
  </div>
<script>
  const chatEl=document.getElementById('chat'),form=document.getElementById('f'),input=document.getElementById('msg');
  let currentMode='general';
  const modeTabs=document.querySelectorAll('.mode-tab');
  const MODE_GREETINGS={
    general:'\u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd! \u0ba8\u0bbe\u0ba9\u0bcd Quantum AI. \u0b8e\u0ba9\u0bcd\u0ba9 \u0c89\u0ba4\u0bb5\u0bbf \u0bb5\u0bc7\u0ba3\u0bc1\u0bae\u0bcd? :)',
    coding:'Coding AI mode. Enna code venum, sollunga - Python, JS, Java edhuvum kelvi kekkalaam!',
    resume:'Resume AI mode. Unga role, experience, skills sollunga - nalla resume section ezhuthi tharen.',
    roleplay:'Roleplay AI mode. Enna character or scenario venum sollunga, adhula ninnu pesuvom!',
    image:'Image AI mode. Oru photo attach pannunga (+ button), adhu pathi kelvi kekkalaam.'
  };
  modeTabs.forEach(t=>t.addEventListener('click', ()=>{
    modeTabs.forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    currentMode=t.dataset.mode;
    chatEl.innerHTML='';
    addBubble(MODE_GREETINGS[currentMode]||MODE_GREETINGS.general,'bot');
  }));
  const sendBtn=document.getElementById('sendBtn'),plusBtn=document.getElementById('plusBtn');
  const plusMenu=document.getElementById('plusMenu'),menuAddFile=document.getElementById('menuAddFile');
  const fileInput=document.getElementById('fileInput'),chip=document.getElementById('attach-chip');
  const chipImg=document.getElementById('chip-img'),chipName=document.getElementById('chip-name');
  const chipRemove=document.getElementById('chip-remove');
  const artifactPanel=document.getElementById('artifactPanel'),artTitle=document.getElementById('artTitle');
  const artLang=document.getElementById('artLang'),artBody=document.getElementById('artBody');
  const artTabs=document.getElementById('artTabs'),artClose=document.getElementById('artClose');
  const artCopy=document.getElementById('artCopy'),artDownload=document.getElementById('artDownload');
  let artifacts=[]; // {lang, ext, code}
  let attachedPreviewB64=null;
  plusBtn.addEventListener('click',e=>{e.stopPropagation();plusMenu.classList.toggle('show');});
  document.addEventListener('click',()=>plusMenu.classList.remove('show'));
  menuAddFile.addEventListener('click',()=>{plusMenu.classList.remove('show');fileInput.click();});
  function escapeHtml(s){
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  const EXT_MAP = {python:'py',py:'py',javascript:'js',js:'js',typescript:'ts',ts:'ts',java:'java',
    c:'c',cpp:'cpp','c++':'cpp',csharp:'cs',cs:'cs',go:'go',ruby:'rb',rb:'rb',php:'php',
    html:'html',css:'css',json:'json',xml:'xml',sql:'sql',bash:'sh',sh:'sh',shell:'sh',yaml:'yaml',
    text:'txt','':'txt'};

  function openArtifact(i){
    if(!artifacts.length) return;
    artifactPanel.classList.add('open');
    artTabs.innerHTML='';
    artifacts.forEach((a,idx)=>{
      const t=document.createElement('div'); t.className='art-tab'+(idx===i?' active':'');
      t.textContent=(a.lang||'code')+' #'+(idx+1); t.addEventListener('click',()=>openArtifact(idx));
      artTabs.appendChild(t);
    });
    const a=artifacts[i];
    artTitle.textContent='quantum_code_'+(i+1)+'.'+a.ext;
    artLang.textContent=(a.lang||'code').toUpperCase();
    artBody.textContent=a.code;
    artCopy.onclick=()=>{navigator.clipboard.writeText(a.code);artCopy.textContent='[OK] Copied';
      setTimeout(()=>artCopy.textContent='[Copy] Copy',1500);};
    artDownload.onclick=()=>{
      const blob=new Blob([a.code],{type:'text/plain'});
      const link=document.createElement('a'); link.href=URL.createObjectURL(blob);
      link.download='quantum_code_'+(i+1)+'.'+a.ext; link.click();
    };
  }
  artClose.addEventListener('click',()=>artifactPanel.classList.remove('open'));

  const BT = String.fromCharCode(96); // backtick character, built safely to survive copy-paste
  const FENCE = BT + BT + BT;
  function addBubble(text,who,imgB64){
    const d=document.createElement('div');d.className='bubble '+who;
    if(imgB64){const img=document.createElement('img');img.src='data:image/png;base64,'+imgB64;d.appendChild(img);}
    if(who.indexOf('bot')===0 && text.indexOf(FENCE)!==-1){
      renderWithCodeBlocks(d, text);
    } else {
      const t=document.createElement('div');t.textContent=text;d.appendChild(t);
    }
    chatEl.appendChild(d);chatEl.scrollTop=chatEl.scrollHeight;return d;
  }
  function renderWithCodeBlocks(container, text){
    const re = new RegExp(FENCE + "(\\w*)\\n([\\s\\S]*?)" + FENCE, "g");
    let last=0, m;
    while((m=re.exec(text))!==null){
      if(m.index>last){
        const p=document.createElement('div'); p.textContent=text.slice(last,m.index); container.appendChild(p);
      }
      const lang=(m[1]||'text').toLowerCase(), code=m[2];
      const ext=EXT_MAP[lang]||'txt';
      const artIndex=artifacts.length; artifacts.push({lang,ext,code});
      const block=document.createElement('div'); block.className='code-block';
      const head=document.createElement('div'); head.className='code-head';
      head.innerHTML='<span class="lang">'+(lang||'code')+'</span><span class="actions">'+
        '<button type="button" class="copy-btn">\uD83D\uDCCB Copy</button>'+
        '<button type="button" class="dl-btn">\u2B07 Download</button>'+
        '<button type="button" class="open-panel-btn">\uD83D\uDDD4 Open panel</button></span>';
      const preview=document.createElement('pre');
      const lines=code.split('\\n'); preview.textContent=lines.slice(0,6).join('\\n')+(lines.length>6?'\\n...':'');
      block.appendChild(head); block.appendChild(preview); container.appendChild(block);
      head.querySelector('.copy-btn').addEventListener('click',()=>{
        navigator.clipboard.writeText(code);
        const b=head.querySelector('.copy-btn'); b.textContent='[OK] Copied'; setTimeout(()=>b.textContent='[Copy] Copy',1500);
      });
      head.querySelector('.dl-btn').addEventListener('click',()=>{
        const blob=new Blob([code],{type:'text/plain'});
        const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
        a.download='quantum_code_'+(artIndex+1)+'.'+ext; a.click();
      });
      head.querySelector('.open-panel-btn').addEventListener('click',()=>openArtifact(artIndex));
      last=re.lastIndex;
    }
    if(last<text.length){
      const p=document.createElement('div'); p.textContent=text.slice(last); container.appendChild(p);
    }
    if(artifacts.length) openArtifact(artifacts.length-1);
  }
  addBubble('வணக்கம்! நான் Quantum AI. என்ன உதவி வேணும்? :)','bot');
  function showChip(name,b64){chip.classList.add('show');chipName.textContent=name;
    if(b64){chipImg.src='data:image/png;base64,'+b64;chipImg.style.display='block';}else{chipImg.style.display='none';}}
  function hideChip(){chip.classList.remove('show');attachedPreviewB64=null;}
  chipRemove.addEventListener('click',async()=>{hideChip();await fetch('/api/clear_attachment',{method:'POST'});});
  fileInput.addEventListener('change',async()=>{
    const f=fileInput.files[0];if(!f)return;
    const fd=new FormData();fd.append('file',f);plusBtn.disabled=true;
    try{
      const res=await fetch('/api/upload',{method:'POST',body:fd});const data=await res.json();
      if(data.success){attachedPreviewB64=data.preview_b64||null;showChip(data.name,attachedPreviewB64);}
      else{alert('Upload failed: '+(data.error||'unknown error'));}
    }catch(err){alert('Upload error: '+err);}
    plusBtn.disabled=false;fileInput.value='';
  });
  form.addEventListener('submit',async(e)=>{
    e.preventDefault();const text=input.value.trim();
    if(!text&&!chip.classList.contains('show'))return;
    addBubble(text||'(attachment)','user',attachedPreviewB64);input.value='';hideChip();
    sendBtn.disabled=true;const loadingBubble=addBubble('typing...','bot loading');
    try{
      const res=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text, mode:currentMode})});
      const data=await res.json();
      loadingBubble.classList.remove('loading','bot');loadingBubble.remove();
      addBubble(data.reply||'[No response]','bot');
    }catch(err){
      loadingBubble.classList.remove('loading');
      loadingBubble.textContent='[Connection error: '+err+']';
    }
    sendBtn.disabled=false;input.focus();
  });
</script>
</body></html>
"""


@app.route("/ai")
@login_required
def ai_page():
    return safe_render(AI_PAGE_HTML)


# ---------------------------------------------------------------- AI API
pending_attachment = {}  # per-session: {user_id: {...}}


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    uid = session["user_id"]
    f = request.files.get("file")
    if not f:
        return jsonify({"success": False, "error": "No file"})
    name = f.filename or "file"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = f.read()

    if ext in IMAGE_EXTS:
        b64 = base64.b64encode(data).decode("utf-8")
        pending_attachment[uid] = {"type": "image", "b64": b64,
                                    "mime": MIME_MAP.get(ext, "image/png"), "name": name}
        return jsonify({"success": True, "name": name, "preview_b64": b64})

    if ext == "pdf":
        if not fitz:
            return jsonify({"success": False, "error": "pymupdf not installed"})
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            text = "".join(page.get_text() for page in doc)[:15000]
            doc.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
        pending_attachment[uid] = {"type": "text", "content": f"[PDF: {name}]\n{text}", "name": name}
        return jsonify({"success": True, "name": name})

    if ext == "csv":
        if not pd:
            return jsonify({"success": False, "error": "pandas not installed"})
        try:
            df = pd.read_csv(io.BytesIO(data))
            summary = (f"Dataset: {name}\nShape: {df.shape}\n\n"
                       f"Columns:\n{df.dtypes.to_string()}\n\n"
                       f"First 5 rows:\n{df.head().to_string()}\n\n"
                       f"Summary:\n{df.describe(include='all').to_string()}")[:4000]
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
        pending_attachment[uid] = {"type": "text", "content": f"[CSV: {name}]\n{summary}", "name": name}
        return jsonify({"success": True, "name": name})

    if ext in ("txt", "md"):
        text = data.decode("utf-8", errors="ignore")[:15000]
        pending_attachment[uid] = {"type": "text", "content": f"[File: {name}]\n{text}", "name": name}
        return jsonify({"success": True, "name": name})

    return jsonify({"success": False, "error": f"Unsupported file type: .{ext}"})


@app.route("/api/clear_attachment", methods=["POST"])
@login_required
def clear_attachment():
    pending_attachment.pop(session["user_id"], None)
    return jsonify({"status": "cleared"})


@app.route("/api/ask", methods=["POST"])
@login_required
def ask():
    uid = session["user_id"]
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    mode = (data.get("mode") or "general").strip()
    sys_prompt = build_system_prompt(mode)

    if not groq_client:
        return jsonify({"reply": "[Quantum AI not configured. Set GROQ_API_KEY on the server.]"})

    db = get_db()
    history_rows = db.execute(
        "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (uid,)
    ).fetchall()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]

    attachment = pending_attachment.pop(uid, None)

    try:
        if attachment and attachment["type"] == "image":
            prompt_text = message or "இந்த image-ல என்ன இருக்கு? விவரமா சொல்லு."
            resp = groq_client.chat.completions.create(
                model=GROQ_VISION_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {
                            "url": f"data:{attachment['mime']};base64,{attachment['b64']}"
                        }},
                        {"type": "text", "text": prompt_text},
                    ]},
                ],
                max_tokens=4096,
            )
            reply = resp.choices[0].message.content
            db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'user', ?)",
                       (uid, f"[image attached] {prompt_text}"))
            db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'assistant', ?)",
                       (uid, reply))
            db.commit()
            return jsonify({"reply": reply})

        if attachment and attachment["type"] == "text":
            combined = attachment["content"]
            if message:
                combined += f"\n\nUser question: {message}"
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": sys_prompt}] + history +
                         [{"role": "user", "content": combined}],
                max_tokens=4096,
            )
            reply = resp.choices[0].message.content
            db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'user', ?)", (uid, combined))
            db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'assistant', ?)", (uid, reply))
            db.commit()
            return jsonify({"reply": reply})

        if not message:
            return jsonify({"reply": ""})

        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": sys_prompt}] + history +
                     [{"role": "user", "content": message}],
            max_tokens=4096,
        )
        reply = resp.choices[0].message.content
        db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'user', ?)", (uid, message))
        db.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, 'assistant', ?)", (uid, reply))
        db.commit()
        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"reply": f"[Error: {e}]"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
