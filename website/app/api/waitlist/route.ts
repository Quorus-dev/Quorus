import { after } from "next/server";
import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  let email: string | undefined;
  try {
    ({ email } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (!email || !email.includes("@") || email.length > 254) {
    return NextResponse.json({ error: "Invalid email" }, { status: 400 });
  }

  // Respond immediately — do the email work in the background
  after(async () => {
    const ts = new Date().toISOString();
    const resendKey = process.env.RESEND_API_KEY;
    const notifyTo = process.env.NOTIFY_EMAIL ?? "arav@getmurmur.ai";
    const fromEmail = process.env.RESEND_FROM ?? "waitlist@getmurmur.ai";

    if (resendKey) {
      const { Resend } = await import("resend");
      const resend = new Resend(resendKey);

      // Notify founders — fire and forget
      resend.emails
        .send({
          from: fromEmail,
          to: notifyTo,
          subject: `Murmur waitlist: ${email}`,
          html: `<p><strong>${email}</strong> joined the Murmur waitlist.</p><p style="color:#888">${ts}</p>`,
        })
        .catch((err: unknown) =>
          console.error("[waitlist] notify send error:", err),
        );

      // Confirmation to user
      resend.emails
        .send({
          from: fromEmail,
          to: email!,
          subject: "You're on the Murmur waitlist",
          html: `
          <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;background:#06060a;color:#fff;">
            <div style="margin-bottom:28px;">
              <span style="font-family:monospace;font-size:18px;font-weight:700;color:#a78bfa;">murmur</span>
            </div>
            <h2 style="font-size:26px;font-weight:700;margin:0 0 12px;letter-spacing:-0.02em;">You're on the list.</h2>
            <p style="color:rgba(255,255,255,0.55);line-height:1.7;margin:0 0 28px;font-size:15px;">
              We review every application personally and are onboarding early teams now.
              We'll be in touch shortly.
            </p>
            <p style="color:rgba(255,255,255,0.2);font-size:12px;margin:0;">
              No spam, ever. Unsubscribe anytime by replying to this email.
            </p>
          </div>`,
        })
        .catch((err: unknown) =>
          console.error("[waitlist] confirm send error:", err),
        );
    } else {
      // Fallback logging until Resend is configured
      // Set RESEND_API_KEY + RESEND_FROM in Vercel project settings
      console.log(`[waitlist] ${ts} — ${email}`);
    }

    // Formspree fallback if configured
    const formspreeEndpoint = process.env.FORMSPREE_ENDPOINT;
    if (formspreeEndpoint) {
      fetch(formspreeEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ email }),
      }).catch((err) => console.error("[waitlist] formspree error:", err));
    }
  });

  return NextResponse.json({ ok: true });
}
