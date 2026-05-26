const { onDocumentCreated } = require("firebase-functions/v2/firestore");
const { defineSecret } = require("firebase-functions/params");
const admin = require("firebase-admin");
const nodemailer = require("nodemailer");

admin.initializeApp();

const gmailPassword = defineSecret("GMAIL_APP_PASSWORD");

const SITE_URL = "https://tvsearch.uk";
const GMAIL_USER = "tvsearchuk@gmail.com";

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
