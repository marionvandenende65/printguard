"""
PrintGuard — E-mail via SiteGround SMTP
Configureer via .env:
  MAIL_HOST     bijv. mail.printguardtool.com
  MAIL_PORT     465 (SSL) of 587 (STARTTLS)
  MAIL_USER     noreply@printguardtool.com
  MAIL_PASSWORD  wachtwoord van het mailaccount
  MAIL_FROM     PrintGuard <noreply@printguardtool.com>
"""

import smtplib, ssl, os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

MAIL_HOST     = os.getenv("MAIL_HOST",     "")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "465"))
MAIL_USER     = os.getenv("MAIL_USER",     "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM",     "PrintGuard <noreply@printguardtool.com>")


def _send(to: str, subject: str, html: str) -> bool:
    """Verstuur een e-mail via SiteGround SMTP. Geeft False terug bij fout."""
    if not MAIL_HOST or not MAIL_USER or not MAIL_PASSWORD:
        print(f"[mail] SMTP niet geconfigureerd — mail aan {to} niet verstuurd")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = MAIL_FROM
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if MAIL_PORT == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT, context=ctx) as smtp:
                smtp.login(MAIL_USER, MAIL_PASSWORD)
                smtp.sendmail(MAIL_USER, to, msg.as_string())
        else:
            with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(MAIL_USER, MAIL_PASSWORD)
                smtp.sendmail(MAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"[mail] Fout bij versturen naar {to}: {e}")
        return False


def send_welcome(to: str, name: str, plan: str, billing: str) -> bool:
    plan_labels = {
        "starter":      "Starter",
        "professional": "Professional",
        "studio":       "Studio",
    }
    billing_label = "maandelijks" if billing == "monthly" else "jaarlijks"
    plan_label    = plan_labels.get(plan, plan.capitalize())

    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #0f0e0d;">
      <div style="border-bottom: 1px solid #e4ddd2; padding-bottom: 24px; margin-bottom: 32px;">
        <span style="font-family: monospace; font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase;">
          PRINT<span style="color: #c8531a;">GUARD</span>
        </span>
      </div>
      <h2 style="font-weight: 300; font-size: 28px; margin: 0 0 16px;">Welkom, {name}.</h2>
      <p style="color: #3a3834; line-height: 1.7;">
        Uw account is actief. U heeft het <strong>{plan_label}</strong>-plan ({billing_label}).
      </p>
      <p style="color: #3a3834; line-height: 1.7; margin-top: 16px;">
        Log in op <a href="https://www.printguardtool.com" style="color: #c8531a;">printguardtool.com</a>
        om uw eerste kunstwerk te beschermen.
      </p>
      <div style="margin-top: 40px; padding-top: 24px; border-top: 1px solid #e4ddd2;
                  font-family: monospace; font-size: 11px; color: #7a776f; letter-spacing: 0.05em;">
        PrintGuard · Art Protection Technology<br>
        U ontvangt deze mail omdat u zich heeft aangemeld op printguardtool.com
      </div>
    </div>
    """
    return _send(to, f"Welkom bij PrintGuard — {plan_label}", html)


def send_contact(name: str, email: str, subject: str, message: str) -> bool:
    """Stuur een contactformulier-bericht door naar info@printguardtool.com."""
    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #0f0e0d;">
      <div style="border-bottom: 1px solid #e4ddd2; padding-bottom: 24px; margin-bottom: 32px;">
        <span style="font-family: monospace; font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase;">
          PRINT<span style="color: #c8531a;">GUARD</span> — Contactformulier
        </span>
      </div>
      <table style="width:100%; border-collapse:collapse; font-family:monospace; font-size:12px; margin-bottom:24px;">
        <tr><td style="padding:8px 0; color:#7a776f; width:120px;">Naam</td><td style="padding:8px 0;">{name}</td></tr>
        <tr><td style="padding:8px 0; color:#7a776f;">E-mail</td><td style="padding:8px 0;"><a href="mailto:{email}" style="color:#c8531a;">{email}</a></td></tr>
        <tr><td style="padding:8px 0; color:#7a776f;">Onderwerp</td><td style="padding:8px 0;">{subject}</td></tr>
      </table>
      <div style="background:#f7f4ef; border:1px solid #e4ddd2; padding:20px; line-height:1.8; color:#3a3834; font-size:14px; white-space:pre-wrap;">{message}</div>
      <div style="margin-top: 40px; padding-top: 24px; border-top: 1px solid #e4ddd2;
                  font-family: monospace; font-size: 11px; color: #7a776f; letter-spacing: 0.05em;">
        Verzonden via printguardtool.com/contact
      </div>
    </div>
    """
    return _send("info@printguardtool.com", f"[PrintGuard contact] {subject}", html)
