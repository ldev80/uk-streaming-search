#!/usr/bin/env python3
"""Send verification emails and match notifications for title alerts."""

import json
import os
from pathlib import Path

import requests
import firebase_admin
from firebase_admin import firestore

PROJECT = Path(__file__).parent
NEW_TITLES = PROJECT / "new_titles.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "tvsearch.uk <alerts@tvsearch.uk>"
SITE_URL = "https://tvsearch.uk"

SERVICE_LABELS = {
    "bbc_iplayer": "BBC iPlayer",
    "itvx": "ITVX",
    "channel4": "Channel 4",
    "channel5": "Channel 5",
    "pbs_america": "PBS America",
    "pluto_tv_uk": "Pluto TV UK",
    "tubi": "Tubi",
    "tptv_encore": "TPTV Encore",
    "uktv_play": "UKTV Play",
    "filmzie": "Filmzie",
    "rakuten_tv": "Rakuten TV",
    "wedotv": "Wedotv",
}


def init_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": "streamfind-2abe7"})
    return firestore.client()


def send_email(to, subject, html_body):
    if not RESEND_API_KEY:
        print(f"  RESEND_API_KEY not set — would send to {to}: {subject}")
        return False
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html_body,
        },
    )
    resp.raise_for_status()
    return True


def send_verifications(db):
    alerts = (
        db.collection("alerts")
        .where(filter=firestore.FieldFilter("verified", "==", False))
        .where(filter=firestore.FieldFilter("verification_sent", "==", False))
        .stream()
    )
    count = 0
    for doc in alerts:
        data = doc.to_dict()
        email = data.get("email", "")
        title = data.get("title_display", data.get("title_lower", ""))
        token = doc.id

        verify_url = f"{SITE_URL}/verify.html?token={token}"
        unsubscribe_url = f"{SITE_URL}/unsubscribe.html?token={token}"
        html = f"""\
<p>You requested an alert for <strong>{title}</strong> on tvsearch.uk.</p>
<p><a href="{verify_url}" style="display:inline-block;padding:10px 20px;background:#D8533D;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">Confirm Alert</a></p>
<p style="color:#888;font-size:13px;">If you didn't request this, ignore this email or <a href="{unsubscribe_url}">remove it</a>.</p>
<p style="color:#888;font-size:12px;">tvsearch.uk</p>"""

        if send_email(email, f"Confirm your alert for \"{title}\"", html):
            doc.reference.update({"verification_sent": True})
            count += 1
            print(f"  Verification sent to {email} for '{title}'")

    return count


def check_matches(db):
    if not NEW_TITLES.exists():
        return 0

    new_titles = json.loads(NEW_TITLES.read_text())
    if not new_titles:
        return 0

    new_lookup = {}
    for item in new_titles:
        key = item.get("title", "").strip().lower()
        if key:
            new_lookup.setdefault(key, []).append(item)

    alerts = db.collection("alerts").where(filter=firestore.FieldFilter("verified", "==", True)).stream()
    count = 0
    for doc in alerts:
        data = doc.to_dict()
        title_lower = data.get("title_lower", "")
        if title_lower not in new_lookup:
            continue

        email = data.get("email", "")
        title_display = data.get("title_display", title_lower)
        matches = new_lookup[title_lower]
        token = doc.id

        services_html = ""
        for m in matches:
            svc = SERVICE_LABELS.get(m.get("service", ""), m.get("service", ""))
            url = m.get("url", "#")
            services_html += f'<li><a href="{url}">{svc}</a></li>'

        unsubscribe_url = f"{SITE_URL}/unsubscribe.html?token={token}"
        html = f"""\
<p><strong>{title_display}</strong> is now available for free streaming!</p>
<ul>{services_html}</ul>
<p><a href="{SITE_URL}/?q={title_display}">Search on tvsearch.uk</a></p>
<p style="color:#888;font-size:12px;"><a href="{unsubscribe_url}">Unsubscribe</a> &middot; tvsearch.uk</p>"""

        if send_email(email, f"\"{title_display}\" is now free to stream!", html):
            doc.reference.delete()
            count += 1
            print(f"  Match notification sent to {email} for '{title_display}'")

    return count


def main():
    db = init_firebase()

    v = send_verifications(db)
    m = check_matches(db)

    if v or m:
        print(f"Sent {v} verifications, {m} match notifications")


if __name__ == "__main__":
    main()
