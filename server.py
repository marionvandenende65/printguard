"""
PrintGuard Server v5
- SQLite database + bcrypt wachtwoorden
- JWT sessies (persistent over herstarts)
- Wachtwoord-reset via e-mail
- Certificaat-archief (ZIP opslaan per gebruiker)
- Batch upload (meerdere afbeeldingen tegelijk)
- Referral-codes
- Demo zonder account (IP rate-limited)
- Opzegging via account
- 500MB upload limiet
- Mollie checkout (actief zodra MOLLIE_API_KEY is ingesteld)
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
from watermark import embed_watermark, detect_watermark, embed_dct_watermark, detect_dct_watermark
from users import (
    check_password, get_usage, can_upload, record_upload,
    has_feature, get_user, create_user, upgrade_user, PLANS,
    create_reset_token, consume_reset_token,
    cancel_subscription,
    get_referral_code, validate_referral_code, get_referral_stats,
    store_certificate, list_certificates, get_certificate_zip,
    can_demo, record_demo,
)
from mail import send_welcome, send_contact, send_reset_email, send_cancel_confirm

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

SECRET_KEY   = os.getenv("SECRET_KEY", "dev_secret_change_in_production")
ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN", "")
TOKEN_DAYS   = 30
MOLLIE_KEY   = os.getenv("MOLLIE_API_KEY", "")
SITE_URL     = os.getenv("SITE_URL", "https://www.printguardtool.com")

init_db()


# ── OpenTimestamps ────────────────────────────────────────────────────────────

_OTS_CALENDARS = [
    "https://a.pool.opentimestamps.org/digest",
    "https://b.pool.opentimestamps.org/digest",
    "https://alice.btc.calendar.opentimestamps.org/digest",
]

def _stamp_to_ots(hash_hex: str) -> tuple[bytes, str]:
    hash_bytes = bytes.fromhex(hash_hex)
    for url in _OTS_CALENDARS:
        try:
            req = urllib.request.Request(
                url,
                data=hash_bytes,
                headers={"Content-Type": "application/octet-stream",
                         "Accept": "application/vnd.opentimestamps.v1"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.read(), url
        except Exception as e:
            print(f"[ots] {url} mislukt: {e}")
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
    return jsonify({"ok": True})


# ── Wachtwoord reset ──────────────────────────────────────────────────────────

@app.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data  = request.json or {}
    email = data.get("email", "").lower().strip()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Ongeldig e-mailadres"}), 400

    token = create_reset_token(email)
    if token:
        user = get_user(email)
        name = (user.get("name") or email.split("@")[0]) if user else email.split("@")[0]
        link = f"{SITE_URL}/reset-password?token={token}"
        send_reset_email(email, name, link)

    # Altijd succes tonen — geen user enumeration
    return jsonify({"ok": True})


@app.route("/api/reset-password", methods=["POST"])
def reset_password():
    data     = request.json or {}
    token    = data.get("token", "").strip()
    password = data.get("password", "")
    if not token or len(password) < 8:
        return jsonify({"ok": False, "error": "Token en wachtwoord (min. 8 tekens) vereist"}), 400

    ok = consume_reset_token(token, password)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Ongeldige of verlopen link"}), 400


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
        usage  = get_usage(email)
        limit  = usage["limit"]
        return None, None, (jsonify({
            "error":   "limit_reached",
            "message": f"Maandlimiet bereikt ({limit} uploads). Upgrade naar een hoger plan.",
            "plan":    usage["plan"],
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


def _do_protect(img_bytes: bytes, email: str):
    """Verwerk één afbeelding — geeft (protected_PIL, w, h, elapsed) terug."""
    from PIL import Image
    import numpy as _np
    from PIL import Image as _PIL

    img = Image.open(io.BytesIO(img_bytes))
    pattern        = request.form.get("pattern", "combined")
    strength       = max(3,  min(45, int(request.form.get("strength", 18))))
    channel_split  = max(0,  min(30, int(request.form.get("p1", 12))))
    freq_variation = max(1,  min(8,  int(request.form.get("p2",  3))))
    printer_target = "all"
    ai_seed = int(sha256_of_bytes(img_bytes)[:8], 16) % (2**32)

    t0        = time.time()
    protected = apply_protection(img, pattern=pattern, strength=strength,
                                 channel_split=channel_split, freq_variation=freq_variation,
                                 printer_target=printer_target, ai_seed=ai_seed)
    elapsed = round(time.time() - t0, 2)

    user     = get_user(email)
    uname    = (user.get("name") or email.split("@")[0]) if user else email.split("@")[0]
    img_hash = sha256_of_bytes(img_bytes)
    cert_id  = f"PG-{img_hash[:12].upper()}"

    prot_arr = _np.array(protected)
    prot_arr = embed_watermark(prot_arr, uname, cert_id, img_hash)
    prot_arr = embed_dct_watermark(prot_arr, uname, cert_id, img_hash)
    protected = _PIL.fromarray(prot_arr, "RGB")
    if img.mode == "RGBA":
        protected.putalpha(img.split()[3])

    w, h = img.size
    return protected, w, h, elapsed


# ── Bescherming (enkelvoudig) ─────────────────────────────────────────────────

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
            "message": f"Uw afbeelding ({w}×{h}px) overschrijdt het maximum ({max_px}px).",
            "max_px":  max_px,
        }), 400

    protected, w, h, elapsed = _do_protect(img_bytes, email)
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


# ── Bescherming (batch) ───────────────────────────────────────────────────────

@app.route("/api/protect-batch", methods=["POST"])
def protect_batch():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    if not has_feature(email, "batch"):
        return jsonify({"error": "Batch-upload vereist het Professional of Studio plan"}), 403

    files = request.files.getlist("images[]")
    if not files:
        return jsonify({"error": "Geen afbeeldingen meegestuurd"}), 400
    if len(files) > 20:
        return jsonify({"error": "Maximum 20 afbeeldingen per batch"}), 400

    usage_data = get_usage(email)
    max_px     = usage_data["max_px"]
    remaining  = usage_data.get("remaining")  # None = onbeperkt

    if remaining is not None and remaining < len(files):
        return jsonify({
            "error":   "limit_reached",
            "message": f"Onvoldoende uploads resterend ({remaining}). U wilt {len(files)} afbeeldingen verwerken.",
        }), 429

    zip_buf = io.BytesIO()
    errors  = []
    done    = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        from PIL import Image
        for i, f in enumerate(files):
            try:
                img_bytes = f.read()
                img       = Image.open(io.BytesIO(img_bytes))
                w, h      = img.size
                if w > max_px or h > max_px:
                    errors.append(f"{f.filename}: te groot ({w}×{h}px, max {max_px}px)")
                    continue
                protected, *_ = _do_protect(img_bytes, email)
                record_upload(email)
                buf = io.BytesIO()
                protected.save(buf, "PNG", compress_level=6)
                name = f.filename.rsplit(".", 1)[0] if f.filename else f"afbeelding_{i+1}"
                zf.writestr(f"{name}_beschermd.png", buf.getvalue())
                done += 1
            except Exception as e:
                errors.append(f"{f.filename}: {e}")

    if done == 0:
        return jsonify({"error": "Geen afbeeldingen verwerkt", "details": errors}), 400

    zip_buf.seek(0)
    resp = send_file(zip_buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"printguard_batch_{int(time.time())}.zip")
    resp.headers["X-Processed"] = str(done)
    resp.headers["X-Errors"]    = str(len(errors))
    resp.headers["X-Usage"]     = json.dumps(get_usage(email))
    return resp


# ── Demo (geen account vereist) ───────────────────────────────────────────────

@app.route("/api/demo", methods=["POST"])
def demo():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not can_demo(ip):
        return jsonify({
            "error":   "demo_limit",
            "message": "U heeft vandaag al een demo gebruikt. Registreer voor meer uploads.",
        }), 429

    if "image" not in request.files:
        return jsonify({"error": "Geen afbeelding meegestuurd"}), 400

    img_bytes = request.files["image"].read()
    if len(img_bytes) > 20 * 1024 * 1024:
        return jsonify({"error": "Demo maximaal 20 MB"}), 400

    from PIL import Image
    import numpy as _np
    from PIL import Image as _PIL

    img  = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    if w > 2400 or h > 2400:
        return jsonify({
            "error":   "resolution_exceeded",
            "message": "Demo is beperkt tot 2400px. Registreer voor hogere resoluties.",
        }), 400

    ai_seed   = int(sha256_of_bytes(img_bytes)[:8], 16) % (2**32)
    protected = apply_protection(img, pattern="combined", strength=18,
                                 channel_split=12, freq_variation=3,
                                 printer_target="all", ai_seed=ai_seed)
    record_demo(ip)

    buf = io.BytesIO()
    protected.save(buf, "PNG", compress_level=6)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True,
                     download_name="printguard_demo.png")


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

    file_hash          = sha256_of_bytes(img_bytes)
    cert_id            = f"PG-{file_hash[:12].upper()}"
    ots_bytes, ots_cal = _stamp_to_ots(file_hash)
    ots_submitted      = len(ots_bytes) > 0

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

    zip_bytes = zip_buf.getvalue()

    # Sla op in archief
    store_certificate(email, cert_id, artwork_title, file_hash, zip_bytes)

    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"printguard_certificaat_{ts}.zip",
    )


# ── Certificaat archief ───────────────────────────────────────────────────────

@app.route("/api/certificates", methods=["GET"])
def certificates_list():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    return jsonify({"certificates": list_certificates(email)})


@app.route("/api/certificates/<cert_id>", methods=["GET"])
def certificate_download(cert_id):
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    zip_bytes = get_certificate_zip(email, cert_id)
    if not zip_bytes:
        return jsonify({"error": "Certificaat niet gevonden"}), 404
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{cert_id}.zip",
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


# ── Watermerk detectie (admin) ────────────────────────────────────────────────

@app.route("/api/detect-watermark", methods=["POST"])
def detect_wm():
    token = request.headers.get("X-Admin-Token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({"error": "Niet geautoriseerd"}), 403
    if "image" not in request.files:
        return jsonify({"error": "Geen afbeelding"}), 400

    img_bytes = request.files["image"].read()
    orig_hash = request.form.get("original_hash", "")
    if not orig_hash or len(orig_hash) != 64:
        return jsonify({"error": "original_hash vereist (SHA-256)"}), 400

    from PIL import Image as _PIL
    import numpy as _np
    img = _PIL.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = _np.array(img)

    result_lsb = detect_watermark(arr, orig_hash)
    if result_lsb.get("found"):
        result_lsb["layer"] = "LSB"
        return jsonify(result_lsb)

    result_dct = detect_dct_watermark(arr, orig_hash)
    if result_dct.get("found"):
        return jsonify(result_dct)

    return jsonify({"found": False, "reason": "Geen PrintGuard-watermerk gevonden"})


# ── Opzegging ─────────────────────────────────────────────────────────────────

@app.route("/api/cancel", methods=["POST"])
def cancel():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401

    ok = cancel_subscription(email)
    if ok:
        usage = get_usage(email)
        user  = get_user(email)
        name  = (user.get("name") or email.split("@")[0]) if user else ""
        send_cancel_confirm(email, name, usage["plan"], usage["next_reset"])
        return jsonify({"ok": True, "access_until": usage["next_reset"]})
    return jsonify({"ok": False, "error": "Opzegging mislukt"}), 500


# ── Referral ──────────────────────────────────────────────────────────────────

@app.route("/api/referral", methods=["GET"])
def referral_get():
    email = get_email()
    if not email:
        return jsonify({"error": "Niet geautoriseerd"}), 401
    return jsonify(get_referral_stats(email))


@app.route("/api/referral/validate", methods=["POST"])
def referral_validate():
    data  = request.json or {}
    code  = data.get("code", "").strip()
    owner = validate_referral_code(code)
    return jsonify({"valid": owner is not None})


# ── Contactformulier ──────────────────────────────────────────────────────────

@app.route("/api/contact", methods=["POST"])
def contact():
    data    = request.json or {}
    name    = data.get("name",    "").strip()[:80]
    email   = data.get("email",   "").strip()[:120]
    subject = data.get("subject", "").strip()[:120]
    message = data.get("message", "").strip()[:2000]
    hp      = data.get("website", "")

    if hp:
        return jsonify({"ok": True})

    if not name or not email or not message or "@" not in email:
        return jsonify({"ok": False, "error": "Vul alle verplichte velden in"}), 400

    if not subject:
        subject = "Bericht via contactformulier"

    ok = send_contact(name, email, subject, message)
    return jsonify({"ok": True, "queued": not ok})


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
            "message": "Betalingen worden binnenkort geactiveerd. Neem contact op via info@printguardtool.com.",
        }), 503

    data    = request.json or {}
    plan    = data.get("plan", "").lower()
    billing = data.get("billing", "monthly").lower()
    email   = data.get("email", "").lower().strip()
    ref     = data.get("referral", "").strip()

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
            "metadata":    {"plan": plan, "billing": billing,
                            "email": email, "referral": ref},
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
        ref     = meta.get("referral", "")

        if not email:
            return "", 400

        existing = get_user(email)
        if existing:
            upgrade_user(email, plan, billing)
        else:
            name = email.split("@")[0].capitalize()
            create_user(email, _random_password(), name, plan, billing,
                        referred_by=ref)
            send_welcome(email, name, plan, billing)

    except Exception as e:
        print(f"[mollie-webhook] Fout: {e}")
        return "", 500

    return "", 200


def _random_password() -> str:
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


# ── Frontend + routing ────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    full_path  = os.path.join(static_dir, path)
    if path and os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"PrintGuard v5 — http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
