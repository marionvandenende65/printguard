"""
PrintGuard Server v4
- SQLite database + bcrypt wachtwoorden
- JWT sessies (persistent over herstarts)
- /nl/ URL-structuur
- 500MB upload limiet
- Mollie checkout placeholder (actief zodra MOLLIE_API_KEY is ingesteld)
"""

import io, os, time, json, zipfile, urllib.request, urllib.error
import jwt as pyjwt
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, send_file, send_from_directory

from database import init_db
from engine import apply_protection
from certificate import generate_certificate, sha256_of_bytes
from users import (
    check_password, get_usage, can_upload, record_upload,
    has_feature, get_user, create_user, upgrade_user, PLANS,
)
from mail import send_welcome

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

SECRET_KEY   = os.getenv("SECRET_KEY", "dev_secret_change_in_production")
TOKEN_DAYS   = 30
MOLLIE_KEY   = os.getenv("MOLLIE_API_KEY", "")
SITE_URL     = os.getenv("SITE_URL", "https://www.printguardtool.com")

# Database initialiseren bij opstarten
init_db()


# ── OpenTimestamps ────────────────────────────────────────────────────────────

_OTS_CALENDARS = [
    "https://a.pool.opentimestamps.org/digest",
    "https://b.pool.opentimestamps.org/digest",
    "https://alice.btc.calendar.opentimestamps.org/digest",
]

def _stamp_to_ots(hash_hex: str) -> tuple[bytes, str]:
    """
    Dien de SHA-256 hash in bij OpenTimestamps.
    Geeft (ots_bytes, calendar_url) terug, of (b"", "") bij fout.
    """
    hash_bytes = bytes.fromhex(hash_hex)
    for url in _OTS_CALENDARS:
        try:
            req = urllib.request.Request(
                url,
                data=hash_bytes,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/vnd.opentimestamps.v1"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.read(), url
        except Exception as e:
            print(f"[ots] {url} mislukt: {e}")
            continue
    return b"", ""


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_token(email: str) -> str:
    payload = {
        "email": email,
        "exp":   datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS),
    }
    return pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _verify_token(token: str) -> str | None:
    try:
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload.get("email")
    except pyjwt.PyJWTError:
        return None


def get_email() -> str | None:
    return _verify_token(request.headers.get("X-Token", ""))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data  = request.json or {}
    email = data.get("email", "").lower().strip()
    pw    = data.get("password", "")
    if check_password(email, pw):
        token = _create_token(email)
        usage = get_usage(email)
        user  = get_user(email)
        return jsonify({
            "ok":    True,
            "token": token,
            "name":  user.get("name") or email.split("@")[0],
            "usage": usage,
        })
    return jsonify({"ok": False, "error": "Ongeldige gegevens"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    # JWT is stateless — client gooit de token weg
    return jsonify({"ok": True})


# ── Gebruiksinfo ──────────────────────────────────────────────────────────────

@app.route("/api/usage", methods=["GET"])
def usage():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    return jsonify(get_usage(email))


# ── Gedeelde validatie ────────────────────────────────────────────────────────

def _validate_upload():
    email = get_email()
    if not email:
        return None, None, (jsonify({"error": "Niet geautoriseerd", "code": "unauthorized"}), 401)

    if "image" not in request.files:
        return None, None, (jsonify({"error": "Geen afbeelding meegestuurd"}), 400)

    allowed, reason = can_upload(email)
    if not allowed:
        usage = get_usage(email)
        plan  = usage["plan"]
        limit = usage["limit"]
        return None, None, (jsonify({
            "error":   "limit_reached",
            "message": f"Maandlimiet bereikt ({limit} uploads). Upgrade naar een hoger plan voor meer uploads of een jaarabonnement voor onbeperkt gebruik.",
            "plan":    plan,
            "limit":   limit,
        }), 429)

    img_bytes = request.files["image"].read()
    return email, img_bytes, None


def _check_resolution(email, img):
    usage  = get_usage(email)
    max_px = usage["max_px"]
    w, h   = img.size
    if w > max_px or h > max_px:
        return False, w, h, max_px
    return True, w, h, max_px


# ── Bescherming ───────────────────────────────────────────────────────────────

@app.route("/api/protect", methods=["POST"])
def protect():
    email, img_bytes, err = _validate_upload()
    if err:
        return err

    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes))
    ok, w, h, max_px = _check_resolution(email, img)
    if not ok:
        return jsonify({
            "error":   "resolution_exceeded",
            "message": f"Uw afbeelding ({w}×{h}px) overschrijdt het maximum voor uw plan ({max_px}px). Upgrade voor hogere resoluties.",
            "max_px":  max_px,
        }), 400

    pattern        = request.form.get("pattern",        "combined")
    strength       = max(3,  min(45, int(request.form.get("strength",       18))))
    channel_split  = max(0,  min(30, int(request.form.get("channel_split",  12))))
    freq_variation = max(1,  min(8,  int(request.form.get("freq_variation",  3))))
    printer_target = "all"  # altijd breedband: offset + laser + inkjet + AI-proof

    t0 = time.time()
    protected = apply_protection(
        img, pattern=pattern, strength=strength,
        channel_split=channel_split, freq_variation=freq_variation,
        printer_target=printer_target,
    )
    elapsed = round(time.time() - t0, 2)

    record_upload(email)

    buf = io.BytesIO()
    protected.save(buf, "PNG", compress_level=6)
    buf.seek(0)

    resp = send_file(buf, mimetype="image/png", as_attachment=True,
                     download_name="printguard_beschermd.png")
    resp.headers["X-Processing-Time"] = str(elapsed)
    resp.headers["X-Image-Size"]      = f"{w}x{h}"
    resp.headers["X-Megapixels"]      = str(round(w * h / 1e6, 1))
    resp.headers["X-Usage"]           = json.dumps(get_usage(email))
    return resp


# ── Certificaat ───────────────────────────────────────────────────────────────

@app.route("/api/certificate", methods=["POST"])
def certificate():
    email, img_bytes, err = _validate_upload()
    if err:
        return err

    if not has_feature(email, "certificate"):
        return jsonify({
            "error":   "feature_unavailable",
            "message": "Certificaten zijn beschikbaar vanaf het Professional plan.",
            "plan":    get_usage(email)["plan"],
        }), 403

    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes))
    ok, w, h, max_px = _check_resolution(email, img)
    if not ok:
        return jsonify({"error": "resolution_exceeded", "max_px": max_px}), 400

    creator_name  = request.form.get("creator_name",  "Onbekend").strip()
    artwork_title = request.form.get("artwork_title", "Zonder titel").strip()
    lang          = request.form.get("lang", "nl")
    NL            = lang == "nl"

    # SHA-256 hash berekenen en direct indienen bij Bitcoin via OpenTimestamps
    file_hash             = sha256_of_bytes(img_bytes)
    ots_bytes, ots_cal    = _stamp_to_ots(file_hash)
    ots_submitted         = len(ots_bytes) > 0

    pdf_bytes = generate_certificate(
        image_bytes=img_bytes,
        creator_name=creator_name,
        artwork_title=artwork_title,
        image_width=w,
        image_height=h,
        file_size_bytes=len(img_bytes),
        lang=lang,
        ots_submitted=ots_submitted,
        ots_calendar=ots_cal.replace("https://", "").split("/")[0] if ots_cal else "",
    )

    record_upload(email)

    # ZIP met PDF + .ots bewijs
    ts       = int(time.time())
    zip_buf  = io.BytesIO()
    ots_name = "blockchain_bewijs.ots" if NL else "blockchain_proof.ots"
    pdf_name = f"printguard_certificaat_{ts}.pdf"

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(pdf_name, pdf_bytes)
        if ots_submitted:
            zf.writestr(ots_name, ots_bytes)
        else:
            readme = (
                "De blockchain-tijdstempel kon niet worden aangemaakt (netwerk onbereikbaar).\n"
                "Uw certificaat is wel geldig — de SHA-256 hash en tijdstempel zijn vastgelegd in de PDF.\n"
                if NL else
                "The blockchain timestamp could not be created (network unreachable).\n"
                "Your certificate is still valid — the SHA-256 hash and timestamp are recorded in the PDF.\n"
            )
            zf.writestr("LEES_MIJ.txt" if NL else "README.txt", readme)

    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"printguard_certificaat_{ts}.zip",
    )


# ── Hash verificatie ──────────────────────────────────────────────────────────

@app.route("/api/verify-hash", methods=["POST"])
def verify_hash():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    if "image" not in request.files:
        return jsonify({"error": "Geen afbeelding"}), 400
    img_bytes = request.files["image"].read()
    return jsonify({"hash": sha256_of_bytes(img_bytes), "size": len(img_bytes)})


# ── Betaling (Mollie) ─────────────────────────────────────────────────────────

PLAN_PRICES = {
    ("basic",        "monthly"): ("9.00",   "PrintGuard Basic — maandelijks"),
    ("basic",        "yearly"):  ("90.00",  "PrintGuard Basic — jaarlijks"),
    ("starter",      "monthly"): ("19.00",  "PrintGuard Starter — maandelijks"),
    ("starter",      "yearly"):  ("190.00", "PrintGuard Starter — jaarlijks"),
    ("professional", "monthly"): ("49.00",  "PrintGuard Professional — maandelijks"),
    ("professional", "yearly"):  ("490.00", "PrintGuard Professional — jaarlijks"),
    ("studio",       "monthly"): ("99.00",  "PrintGuard Studio — maandelijks"),
    ("studio",       "yearly"):  ("990.00", "PrintGuard Studio — jaarlijks"),
}


@app.route("/api/checkout", methods=["POST"])
def checkout():
    if not MOLLIE_KEY:
        return jsonify({
            "ok":      False,
            "pending": True,
            "message": "Betalingen worden binnenkort geactiveerd. Neem contact op via info@printguardtool.com om vroeg toegang te krijgen.",
        }), 503

    data    = request.json or {}
    plan    = data.get("plan", "").lower()
    billing = data.get("billing", "monthly").lower()
    email   = data.get("email", "").lower().strip()

    if plan not in PLANS or billing not in ("monthly", "yearly") or not email:
        return jsonify({"ok": False, "error": "Ongeldige parameters"}), 400

    amount, description = PLAN_PRICES.get((plan, billing), ("0.00", "PrintGuard"))

    try:
        from mollie.api.client import Client
        mollie = Client()
        mollie.set_api_key(MOLLIE_KEY)

        payment = mollie.payments.create({
            "amount":      {"currency": "EUR", "value": amount},
            "description": description,
            "redirectUrl": f"{SITE_URL}/bedankt",
            "webhookUrl":  f"{SITE_URL}/api/mollie-webhook",
            "metadata":    {"plan": plan, "billing": billing, "email": email},
        })
        return jsonify({"ok": True, "checkout_url": payment["_links"]["checkout"]["href"]})

    except Exception as e:
        print(f"[mollie] Fout: {e}")
        return jsonify({"ok": False, "error": "Betaling kon niet worden aangemaakt"}), 500


@app.route("/api/mollie-webhook", methods=["POST"])
def mollie_webhook():
    if not MOLLIE_KEY:
        return "", 200

    payment_id = request.form.get("id", "")
    if not payment_id:
        return "", 400

    try:
        from mollie.api.client import Client
        mollie = Client()
        mollie.set_api_key(MOLLIE_KEY)

        payment = mollie.payments.get(payment_id)
        if payment["status"] != "paid":
            return "", 200

        meta    = payment.get("metadata", {})
        email   = meta.get("email", "")
        plan    = meta.get("plan", "starter")
        billing = meta.get("billing", "monthly")

        if not email:
            return "", 400

        existing = get_user(email)
        if existing:
            upgrade_user(email, plan, billing)
        else:
            name = email.split("@")[0].capitalize()
            create_user(email, _random_password(), name, plan, billing)
            send_welcome(email, name, plan, billing)

    except Exception as e:
        print(f"[mollie-webhook] Fout: {e}")
        return "", 500

    return "", 200


def _random_password() -> str:
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


# ── Frontend + /nl/ routing ───────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    # Serveer bestaande statische bestanden direct
    full_path = os.path.join(static_dir, path)
    if path and os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(static_dir, path)
    # Alles anders (/, /nl/, /bedankt, etc.) → index.html
    return send_from_directory(static_dir, "index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"PrintGuard v4 — http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
