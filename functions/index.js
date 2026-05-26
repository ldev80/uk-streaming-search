const { onDocumentCreated } = require("firebase-functions/v2/firestore");
const { onSchedule } = require("firebase-functions/v2/scheduler");
const { defineSecret } = require("firebase-functions/params");
const admin = require("firebase-admin");
const nodemailer = require("nodemailer");

admin.initializeApp();

const gmailPassword = defineSecret("GMAIL_APP_PASSWORD");

const SITE_URL = "https://tvsearch.uk";
const GMAIL_USER = "tvsearchuk@gmail.com";

const SERVICE_LABELS = {
  bbc_iplayer: "BBC iPlayer",
  itvx: "ITVX",
  channel4: "Channel 4",
  channel5: "Channel 5",
  pbs_america: "PBS America",
  pluto_tv_uk: "Pluto TV UK",
  tubi: "Tubi",
  tptv_encore: "TPTV Encore",
  uktv_play: "UKTV Play",
  filmzie: "Filmzie",
  rakuten_tv: "Rakuten TV",
  wedotv: "Wedotv",
};

exports.sendVerificationEmail = onDocumentCreated(
  {
    document: "alerts/{token}",
    secrets: [gmailPassword],
  },
  async (event) => {
    const data = event.data.data();
    const token = event.params.token;
    const { email, title_display } = data;

    if (!email || !title_display) return;

    const transporter = nodemailer.createTransport({
      service: "gmail",
      auth: { user: GMAIL_USER, pass: gmailPassword.value() },
    });

    const verifyUrl = `${SITE_URL}/verify.html?token=${token}`;
    const unsubscribeUrl = `${SITE_URL}/unsubscribe.html?token=${token}`;

    await transporter.sendMail({
      from: `tvsearch.uk <${GMAIL_USER}>`,
      to: email,
      subject: `Confirm your alert for "${title_display}"`,
      html: `<p>You requested an alert for <strong>${title_display}</strong> on tvsearch.uk.</p>
<p><a href="${verifyUrl}" style="display:inline-block;padding:10px 20px;background:#60a5fa;color:#000;border-radius:8px;text-decoration:none;font-weight:600;">Confirm Alert</a></p>
<p style="color:#888;font-size:13px;">If you didn't request this, ignore this email or <a href="${unsubscribeUrl}">remove it</a>.</p>
<p style="color:#888;font-size:12px;">tvsearch.uk</p>`,
    });

    await event.data.ref.update({ verification_sent: true });
  }
);

exports.checkAlertMatches = onSchedule(
  {
    schedule: "0 5 * * 0",
    timeZone: "Europe/London",
    secrets: [gmailPassword],
    maxInstances: 1,
  },
  async () => {
    const db = admin.firestore();
    const snapshot = await db
      .collection("alerts")
      .where("verified", "==", true)
      .get();

    if (snapshot.empty) {
      console.log("No verified alerts to check");
      return;
    }

    const res = await fetch(`${SITE_URL}/new_titles.json`);
    if (!res.ok) {
      console.log("No new_titles.json available");
      return;
    }

    const newTitles = await res.json();
    if (!Array.isArray(newTitles) || newTitles.length === 0) {
      console.log("No new titles");
      return;
    }

    const lookup = {};
    for (const item of newTitles) {
      const key = (item.title || "").trim().toLowerCase();
      if (key) {
        if (!lookup[key]) lookup[key] = [];
        lookup[key].push(item);
      }
    }

    const transporter = nodemailer.createTransport({
      service: "gmail",
      auth: { user: GMAIL_USER, pass: gmailPassword.value() },
    });

    let matched = 0;
    const promises = [];

    for (const doc of snapshot.docs) {
      const data = doc.data();
      const titleLower = data.title_lower || "";
      if (!lookup[titleLower]) continue;

      const email = data.email;
      const titleDisplay = data.title_display || titleLower;
      const matches = lookup[titleLower];
      const token = doc.id;

      const servicesHtml = matches
        .map((m) => {
          const label = SERVICE_LABELS[m.service] || m.service;
          const url = m.url || "#";
          return `<li><a href="${url}">${label}</a></li>`;
        })
        .join("");

      const unsubUrl = `${SITE_URL}/unsubscribe.html?token=${token}`;
      const html = `<p><strong>${titleDisplay}</strong> is now available for free streaming!</p>
<ul>${servicesHtml}</ul>
<p><a href="${SITE_URL}/?q=${encodeURIComponent(titleDisplay)}">Search on tvsearch.uk</a></p>
<p style="color:#888;font-size:12px;"><a href="${unsubUrl}">Unsubscribe</a> &middot; tvsearch.uk</p>`;

      promises.push(
        transporter
          .sendMail({
            from: `tvsearch.uk <${GMAIL_USER}>`,
            to: email,
            subject: `"${titleDisplay}" is now free to stream!`,
            html,
            headers: { "List-Unsubscribe": `<${unsubUrl}>` },
          })
          .then(() => doc.ref.delete())
          .then(() => {
            matched++;
          })
          .catch((err) => console.error(`Failed to notify ${email}:`, err))
      );
    }

    await Promise.all(promises);
    console.log(`Checked ${snapshot.size} alerts, sent ${matched} notifications`);
  }
);
