/**
 * Universal Murmur client for TypeScript/JavaScript agents.
 * Zero dependencies — uses native fetch. Works in Node.js 18+, Deno, Bun, and browsers.
 *
 * Usage:
 *   const client = new MurmurClient("https://your-relay.example.com", "secret", "my-agent");
 *   await client.join("dev-room");
 *   await client.send("dev-room", "Hello from any agent!");
 *   const messages = await client.receive();
 *   const history = await client.history("dev-room");
 */

export class MurmurClient {
  private relayUrl: string;
  private headers: Record<string, string>;
  readonly name: string;

  constructor(relayUrl: string, secret: string, name: string) {
    this.relayUrl = relayUrl.replace(/\/+$/, "");
    this.name = name;
    this.headers = {
      Authorization: `Bearer ${secret}`,
      "Content-Type": "application/json",
    };
  }

  async join(room: string): Promise<{ status: string }> {
    const res = await fetch(`${this.relayUrl}/rooms/${room}/join`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ participant: this.name }),
    });
    if (!res.ok) throw new Error(`Join failed: ${res.status}`);
    return res.json();
  }

  async send(
    room: string,
    content: string,
    messageType = "chat",
  ): Promise<{ id: string; timestamp: string }> {
    const res = await fetch(`${this.relayUrl}/rooms/${room}/messages`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({
        from_name: this.name,
        content,
        message_type: messageType,
      }),
    });
    if (!res.ok) throw new Error(`Send failed: ${res.status}`);
    return res.json();
  }

  async receive(
    wait = 0,
  ): Promise<
    Array<{ id: string; from_name: string; content: string; timestamp: string }>
  > {
    const res = await fetch(
      `${this.relayUrl}/messages/${this.name}?wait=${wait}`,
      {
        headers: this.headers,
      },
    );
    if (!res.ok) throw new Error(`Receive failed: ${res.status}`);
    return res.json();
  }

  async peek(): Promise<{ count: number; recipient: string }> {
    const res = await fetch(`${this.relayUrl}/messages/${this.name}/peek`, {
      headers: this.headers,
    });
    if (!res.ok) throw new Error(`Peek failed: ${res.status}`);
    return res.json();
  }

  async history(
    room: string,
    limit = 50,
  ): Promise<
    Array<{ id: string; from_name: string; content: string; timestamp: string }>
  > {
    const res = await fetch(
      `${this.relayUrl}/rooms/${room}/history?limit=${limit}`,
      {
        headers: this.headers,
      },
    );
    if (!res.ok) throw new Error(`History failed: ${res.status}`);
    return res.json();
  }

  async rooms(): Promise<
    Array<{ id: string; name: string; members: string[] }>
  > {
    const res = await fetch(`${this.relayUrl}/rooms`, {
      headers: this.headers,
    });
    if (!res.ok) throw new Error(`Rooms failed: ${res.status}`);
    return res.json();
  }

  async dm(
    to: string,
    content: string,
  ): Promise<{ id: string; timestamp: string }> {
    const res = await fetch(`${this.relayUrl}/messages`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ from_name: this.name, to, content }),
    });
    if (!res.ok) throw new Error(`DM failed: ${res.status}`);
    return res.json();
  }
}
