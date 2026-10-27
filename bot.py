#!/usr/bin/env python3
"""
Telegram Bot — bidirectional messaging.
Polling mode, no external dependencies (only stdlib).

Usage:
    python3 /ssd/tg/bot.py              # run polling (foreground)
    python3 /ssd/tg/bot.py send 731427851 "Hello"  # send message
    python3 /ssd/tg/bot.py send 731427851 --file /path/to/file.txt  # send file content as message
    python3 /ssd/tg/bot.py inbox        # show unread messages
    python3 /ssd/tg/bot.py users        # list known users
"""

import io
import json
import mimetypes
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid
import socket
from datetime import datetime
from pathlib import Path

# Force IPv4 for all outbound connections (Telegram API).
# This host has no working IPv6 route, but api.telegram.org resolves to both
# an A (IPv4) and AAAA (IPv6) record. The default resolver hands back the IPv6
# address first, so every getUpdates/sendMessage failed with
# "Network is unreachable" / "Temporary failure in name resolution".
# We keep normal DNS resolution but drop IPv6 results so only IPv4 is used.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(*args, **kwargs):
    return [ai for ai in _orig_getaddrinfo(*args, **kwargs) if ai[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_only_getaddrinfo

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
INBOX_FILE = BASE_DIR / "inbox.jsonl"
LOG_FILE = BASE_DIR / "bot.log"
PID_FILE = BASE_DIR / "bot.pid"
FILES_DIR = BASE_DIR / "files"

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def api_call(token, method, data=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if data:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log(f"API error {method}: {e.code} {body}")
        return None
    except Exception as e:
        log(f"API exception {method}: {e}")
        return None

def send_message(token, chat_id, text):
    """Send a message. Splits into chunks if > 4096 chars."""
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    results = []
    for chunk in chunks:
        result = api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        })
        # api_call returns None on HTTPError (e.g. 400 "can't parse entities"); fallback must also trigger then.
        if not result or not result.get("ok"):
            result = api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True
            })
        results.append(result)
    return results

def send_photo(token, chat_id, photo_path, caption=None):
    """Send a photo via sendPhoto multipart/form-data.
    Telegram limits: 10MB per photo, max 1280px on long side gets best quality (server may downscale larger).
    Caption max ~1024 chars (truncated if longer).
    """
    if not os.path.isfile(photo_path):
        log(f"sendPhoto: file not found: {photo_path}")
        return None

    boundary = "----TGBoundary" + uuid.uuid4().hex
    body = io.BytesIO()

    def add_field(name, value):
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(str(value).encode("utf-8"))
        body.write(b"\r\n")

    add_field("chat_id", chat_id)
    if caption:
        add_field("caption", caption[:1024])

    filename = os.path.basename(photo_path)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {mime}\r\n\r\n".encode())
    with open(photo_path, "rb") as f:
        body.write(f.read())
    body.write(f"\r\n--{boundary}--\r\n".encode())

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log(f"API error sendPhoto: {e.code} {err_body}")
        return None
    except Exception as e:
        log(f"API exception sendPhoto: {e}")
        return None


def send_document(token, chat_id, file_path, caption=None):
    """Send a file as a document attachment via sendDocument (multipart/form-data)."""
    if not os.path.isfile(file_path):
        log(f"sendDocument: file not found: {file_path}")
        return None
    boundary = "----TGBoundary" + uuid.uuid4().hex
    body = io.BytesIO()

    def add_field(name, value):
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(str(value).encode("utf-8"))
        body.write(b"\r\n")

    add_field("chat_id", chat_id)
    if caption:
        add_field("caption", caption[:1024])

    filename = os.path.basename(file_path)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {mime}\r\n\r\n".encode())
    with open(file_path, "rb") as f:
        body.write(f.read())
    body.write(f"\r\n--{boundary}--\r\n".encode())

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(
        url, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log(f"API error sendDocument: {e.code} {err_body}")
        return None
    except Exception as e:
        log(f"API exception sendDocument: {e}")
        return None


def download_file(token, file_id, save_path):
    """Download a file from Telegram by file_id."""
    result = api_call(token, "getFile", {"file_id": file_id})
    if not result or not result.get("ok"):
        log(f"getFile failed for {file_id}")
        return None
    file_path = result["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(save_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        log(f"Downloaded: {save_path}")
        return str(save_path)
    except Exception as e:
        log(f"Download error: {e}")
        try:
            os.remove(save_path)
        except OSError:
            pass
        return None

def next_seq_number():
    """Get next sequential number by scanning files/ directory."""
    FILES_DIR.mkdir(exist_ok=True)
    max_num = 0
    for f in FILES_DIR.iterdir():
        parts = f.name.split("_", 1)
        if parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    return max_num + 1

def extract_rich_text(message):
    """Extract plain text from Telegram rich_message block format (new text editor).
    paragraph text may be a str or a list of str / {type, text} inline objects;
    list items carry a label plus nested blocks."""
    def flatten(t):
        if isinstance(t, str):
            return t
        if isinstance(t, list):
            return "".join(flatten(x) for x in t)
        if isinstance(t, dict):
            return t.get("text", "")
        return ""

    def walk(blocks, out):
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "list":
                for it in b.get("items", []):
                    sub = []
                    walk(it.get("blocks", []), sub)
                    body = " ".join(s.strip() for s in sub if s.strip())
                    out.append(f"{it.get('label', '')} {body}".strip())
            elif "text" in b:
                out.append(flatten(b["text"]))

    rich = message.get("rich_message")
    if not rich or not rich.get("blocks"):
        return ""
    out = []
    try:
        walk(rich["blocks"], out)
    except Exception as e:
        log(f"rich_message extract error: {e}")
        return ""
    return chr(10).join(out).strip()


def save_incoming(message, token=None):
    """Save incoming message to files/ with sequential numbering."""
    chat = message.get("chat", {})
    from_user = message.get("from", {})
    text = message.get("text", "") or message.get("caption", "")
    if not text:
        text = extract_rich_text(message)
    doc = message.get("document")
    photos = message.get("photo")
    video = message.get("video")
    video_note = message.get("video_note")
    voice = message.get("voice")
    audio = message.get("audio")
    animation = message.get("animation")
    sticker = message.get("sticker")
    ts = datetime.now()
    ts_str = ts.strftime("%Y-%m-%d_%H-%M-%S")

    saved_file = None
    FILES_DIR.mkdir(exist_ok=True)
    seq = next_seq_number()
    prefix = f"{seq:03d}_{ts_str}"

    # Handle photo (take largest)
    if photos and token:
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        photo_path = FILES_DIR / f"{prefix}_photo.jpg"
        saved_file = download_file(token, largest["file_id"], photo_path)
        if text:
            caption_path = FILES_DIR / f"{prefix}_caption.txt"
            caption_path.write_text(text, encoding="utf-8")

    # Handle document
    elif doc and token:
        ext = Path(doc.get("file_name", "file")).suffix or ""
        doc_path = FILES_DIR / f"{prefix}_doc{ext}"
        saved_file = download_file(token, doc["file_id"], doc_path)
        if text:
            caption_path = FILES_DIR / f"{prefix}_caption.txt"
            caption_path.write_text(text, encoding="utf-8")

    # Handle video / video_note / voice / audio / animation / sticker
    elif (video or video_note or voice or audio or animation or sticker) and token:
        if video:
            media, kind, default_ext = video, "video", ".mp4"
        elif video_note:
            media, kind, default_ext = video_note, "video_note", ".mp4"
        elif voice:
            media, kind, default_ext = voice, "voice", ".ogg"
        elif audio:
            media, kind, default_ext = audio, "audio", ".mp3"
        elif animation:
            media, kind, default_ext = animation, "animation", ".mp4"
        else:
            media, kind, default_ext = sticker, "sticker", ".webp"
        ext = Path(media.get("file_name", "")).suffix or default_ext
        media_path = FILES_DIR / f"{prefix}_{kind}{ext}"
        saved_file = download_file(token, media["file_id"], media_path)
        if text:
            caption_path = FILES_DIR / f"{prefix}_caption.txt"
            caption_path.write_text(text, encoding="utf-8")

    # Text-only message
    elif text:
        text_path = FILES_DIR / f"{prefix}_text.txt"
        text_path.write_text(text, encoding="utf-8")

    # Unrecognized message type (contact/dice/poll/location/etc.) - dump raw JSON
    else:
        raw_path = FILES_DIR / f"{prefix}_raw.json"
        raw_path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")
        saved_file = str(raw_path)
        log(f"Unrecognized message type, raw JSON saved: {raw_path}")

    # Save to inbox.jsonl as before
    entry = {
        "ts": ts.isoformat(),
        "message_id": message.get("message_id"),
        "chat_id": chat.get("id"),
        "from_id": from_user.get("id"),
        "from_username": from_user.get("username", ""),
        "from_name": f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip(),
        "text": text,
        "read": False
    }
    if saved_file:
        entry["file"] = saved_file
    if doc:
        entry["file_name"] = doc.get("file_name", "")
        entry["mime_type"] = doc.get("mime_type", "")

    with open(INBOX_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry

def handle_message(config, token, message):
    """Process incoming message."""
    text = message.get("text", "") or message.get("caption", "") or extract_rich_text(message)
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = str(from_user.get("id", ""))

    # Save to inbox (with token for file downloads)
    entry = save_incoming(message, token=token)
    file_info = f" [file: {entry.get('file_name', '')}]" if entry.get("file") else ""
    log(f"Inbox: [{user_id}] @{from_user.get('username', '?')}: {text[:100]}{file_info}")

    # Register user if new
    if user_id and user_id not in config.get("known_users", {}):
        config.setdefault("known_users", {})[user_id] = {
            "username": from_user.get("username", ""),
            "name": f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip(),
            "role": "user"
        }
        save_config(config)
        log(f"New user registered: {user_id} @{from_user.get('username', '')}")

    # Handle /start
    if text == "/start":
        send_message(token, chat_id, "Bot is active. Your messages will be delivered.")
        return

    # Handle /id
    if text == "/id":
        send_message(token, chat_id, f"Your chat_id: `{chat_id}`\nYour user_id: `{user_id}`")
        return

def run_polling(config, token):
    """Main polling loop."""
    log("Starting polling...")
    offset = 0

    # Write PID
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    while True:
        try:
            result = api_call(token, "getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"]
            })
            if result and result.get("ok"):
                for update in result.get("result", []):
                    message = update.get("message")
                    if not message:
                        offset = update["update_id"] + 1
                        continue
                    handled = False
                    for attempt in range(3):
                        try:
                            handle_message(config, token, message)
                            handled = True
                            break
                        except Exception as e:
                            log(f"handle_message error (attempt {attempt + 1}/3, update {update['update_id']}): {e}")
                            time.sleep(2)
                    if not handled:
                        try:
                            dump_path = FILES_DIR / f"failed_update_{update['update_id']}.json"
                            dump_path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")
                            log(f"SKIPPED update {update['update_id']} after 3 failed attempts, raw JSON saved: {dump_path}")
                        except Exception as e:
                            log(f"raw dump failed for update {update['update_id']}: {e}")
                    offset = update["update_id"] + 1
        except KeyboardInterrupt:
            log("Polling stopped by user")
            break
        except Exception as e:
            log(f"Polling error: {e}")
            time.sleep(5)

def cmd_send(token, args):
    """Send message from CLI."""
    if len(args) < 2:
        print("Usage: bot.py send <chat_id> <text>")
        print("       bot.py send <chat_id> --file <path>")
        sys.exit(1)

    chat_id = args[0]
    if args[1] == "--file" and len(args) >= 3:
        with open(args[2]) as f:
            text = f.read()
    else:
        text = " ".join(args[1:])

    results = send_message(token, chat_id, text)
    for r in results:
        if r and r.get("ok"):
            print(f"Sent to {chat_id}: OK")
        else:
            print(f"Sent to {chat_id}: FAILED - {r}")

def cmd_sendphoto(token, args):
    """Send photo from CLI.
    Usage: bot.py sendphoto <chat_id> <path> [caption ...]
    """
    if len(args) < 2:
        print("Usage: bot.py sendphoto <chat_id> <path> [caption]")
        sys.exit(1)
    chat_id = args[0]
    path = args[1]
    caption = " ".join(args[2:]) if len(args) > 2 else None
    result = send_photo(token, chat_id, path, caption)
    if result and result.get("ok"):
        print(f"Photo sent to {chat_id}: OK")
    else:
        print(f"Photo sent to {chat_id}: FAILED - {result}")
        sys.exit(2)


def cmd_sendfile(token, args):
    """Send a file as a document attachment from CLI.
    Usage: bot.py sendfile <chat_id> <path> [caption ...]
    """
    if len(args) < 2:
        print("Usage: bot.py sendfile <chat_id> <path> [caption]")
        sys.exit(1)
    chat_id = args[0]
    path = args[1]
    caption = " ".join(args[2:]) if len(args) > 2 else None
    result = send_document(token, chat_id, path, caption)
    if result and result.get("ok"):
        print(f"File sent to {chat_id}: OK")
    else:
        print(f"File sent to {chat_id}: FAILED - {result}")
        sys.exit(2)


def cmd_inbox():
    """Show unread messages."""
    if not INBOX_FILE.exists():
        print("No messages.")
        return

    messages = []
    with open(INBOX_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))

    unread = [m for m in messages if not m.get("read")]
    if not unread:
        print("No unread messages.")
        return

    for m in unread:
        ts = m.get("ts", "?")[:19]
        from_name = m.get("from_name", "?")
        from_user = m.get("from_username", "")
        text = m.get("text", "")
        chat_id = m.get("chat_id", "")
        print(f"[{ts}] {from_name} (@{from_user}) [chat:{chat_id}]:")
        if text:
            print(f"  {text}")
        if m.get("file"):
            print(f"  FILE: {m.get('file')} ({m.get('file_name', '')} {m.get('mime_type', '')})")
        print()

    # Mark as read
    with open(INBOX_FILE, "w") as f:
        for m in messages:
            m["read"] = True
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"--- {len(unread)} message(s) marked as read ---")

def cmd_users(config):
    """List known users."""
    users = config.get("known_users", {})
    if not users:
        print("No known users.")
        return
    for uid, info in users.items():
        print(f"  {uid}: @{info.get('username', '?')} ({info.get('name', '?')}) role={info.get('role', 'user')}")

def main():
    config = load_config()
    token = config["bot_token"]

    if len(sys.argv) < 2:
        # Default: run polling
        run_polling(config, token)
    elif sys.argv[1] == "send":
        cmd_send(token, sys.argv[2:])
    elif sys.argv[1] == "sendphoto":
        cmd_sendphoto(token, sys.argv[2:])
    elif sys.argv[1] == "sendfile":
        cmd_sendfile(token, sys.argv[2:])
    elif sys.argv[1] == "inbox":
        cmd_inbox()
    elif sys.argv[1] == "users":
        cmd_users(config)
    else:
        print(f"Unknown command: {sys.argv[1]}")
        print("Commands: send, sendphoto, sendfile, inbox, users, or no args for polling")
        sys.exit(1)

if __name__ == "__main__":
    main()
