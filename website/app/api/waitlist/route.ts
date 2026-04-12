import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  let email: string | undefined;
  try {
    ({ email } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (!email || !email.includes("@")) {
    return NextResponse.json({ error: "Invalid email" }, { status: 400 });
  }

  // Forward to Formspree if configured — set FORMSPREE_ENDPOINT in Vercel env vars
  // e.g. https://formspree.io/f/your-form-id
  const endpoint = process.env.FORMSPREE_ENDPOINT;
  if (endpoint) {
    try {
      await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ email }),
      });
    } catch (err) {
      console.error("[waitlist] Formspree error:", err);
    }
  } else {
    // Log until real storage is wired up
    console.log(`[waitlist] ${new Date().toISOString()} — ${email}`);
  }

  return NextResponse.json({ ok: true });
}
