"""CLI tool for viewing MCP Tunnel analytics in the terminal."""

import sys

import httpx
from rich.console import Console
from rich.table import Table

from murmur.config import load_config


def fetch_analytics(relay_url: str, relay_secret: str) -> dict:
    """Fetch analytics data from the relay server."""
    resp = httpx.get(
        f"{relay_url}/analytics",
        headers={"Authorization": f"Bearer {relay_secret}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def render(data: dict) -> None:
    """Render analytics data as terminal tables and charts."""
    console = Console()

    # Summary table
    summary = Table(title="Relay Summary")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total Sent", str(data["total_messages_sent"]))
    summary.add_row("Total Delivered", str(data["total_messages_delivered"]))
    summary.add_row("Pending", str(data["messages_pending"]))
    uptime = data["uptime_seconds"]
    hours, remainder = divmod(uptime, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        uptime_str = f"{hours}h {minutes}m"
    elif minutes:
        uptime_str = f"{minutes}m {secs}s"
    else:
        uptime_str = f"{secs}s"
    summary.add_row("Uptime", uptime_str)
    console.print(summary)
    console.print()

    # Participant table
    if data["participants"]:
        ptable = Table(title="Participants")
        ptable.add_column("Name", style="bold")
        ptable.add_column("Sent", justify="right")
        ptable.add_column("Received", justify="right")
        for name, stats in sorted(data["participants"].items()):
            ptable.add_row(name, str(stats["sent"]), str(stats["received"]))
        console.print(ptable)
        console.print()

    # Hourly volume bar chart
    hourly = data.get("hourly_volume", [])
    if hourly:
        max_count = max(h["count"] for h in hourly)
        bar_width = 40
        console.print("[bold]Hourly Volume (last 72h)[/bold]")
        for entry in hourly:
            hour_label = entry["hour"][11:16]  # Extract HH:MM
            count = entry["count"]
            bar_len = int((count / max_count) * bar_width) if max_count > 0 else 0
            bar = "█" * bar_len
            console.print(f"  {hour_label} │ {bar} {count}")
        console.print()


def main():
    config = load_config()
    relay_url = config.get("relay_url", "http://localhost:8080")
    relay_secret = config.get("relay_secret", "")
    if not relay_secret:
        print("Error: relay_secret not configured. Run: murmur init <name>", file=sys.stderr)
        sys.exit(1)

    try:
        data = fetch_analytics(relay_url, relay_secret)
    except httpx.ConnectError:
        print(f"Error: Cannot reach relay at {relay_url}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"Error: Relay returned {e.response.status_code}", file=sys.stderr)
        sys.exit(1)

    render(data)


if __name__ == "__main__":
    main()
