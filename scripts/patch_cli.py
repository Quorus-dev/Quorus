path = "/Users/aravkekane/Desktop/Quorus/packages/cli/quorus_cli/cli.py"
content = open(path).read()

old_cmd_codex = """def _cmd_codex_agent(args):
    from quorus_cli.codex_agent import CodexAgentError, run_codex_agent

    try:
        rc = run_codex_agent(
            room=args.room,
            relay_url=RELAY_URL,
            parent_name=INSTANCE_NAME,
            parent_api_key=API_KEY,
            relay_secret=RELAY_SECRET,
            requested_name=args.name,
            suffix=args.suffix,
            cwd=Path(args.cwd).resolve(),
            wait_seconds=args.wait,
            announce=args.announce,
            no_launch=args.no_launch,
            verbose=args.verbose,
            sandbox=args.sandbox,
            approval=args.approval,
            autonomous=args.autonomous,
            room_poll_seconds=args.room_poll,
            heartbeat_seconds=args.heartbeat,
            history_limit=args.history_limit,
        )
        sys.exit(rc)
    except CodexAgentError as exc:
        _ui.error("Codex Agent runner failed", hint=str(exc))
        sys.exit(1)"""

new_cmd_gemini = """def _cmd_gemini_agent(args):
    from quorus_cli.gemini_agent import GeminiAgentError, run_gemini_agent

    try:
        rc = run_gemini_agent(
            room=args.room,
            relay_url=RELAY_URL,
            parent_name=INSTANCE_NAME,
            parent_api_key=API_KEY,
            relay_secret=RELAY_SECRET,
            requested_name=args.name,
            suffix=args.suffix,
            cwd=Path(args.cwd).resolve(),
            wait_seconds=args.wait,
            announce=args.announce,
            no_launch=args.no_launch,
            verbose=args.verbose,
            sandbox=args.sandbox,
            approval=args.approval,
            autonomous=args.autonomous,
            room_poll_seconds=args.room_poll,
            heartbeat_seconds=args.heartbeat,
            history_limit=args.history_limit,
        )
        sys.exit(rc)
    except GeminiAgentError as exc:
        _ui.error("Gemini Agent runner failed", hint=str(exc))
        sys.exit(1)"""

if "_cmd_gemini_agent" not in content:
    content = content.replace(old_cmd_codex, old_cmd_codex + "\n\n" + new_cmd_gemini)

old_parser = """    p_codex_agent = sub.add_parser(
        "codex-agent",
        help="Launch Codex bound to a Quorus room (inbox mirror + presence).",
    )
    p_codex_agent.add_argument("room", help="Room name")
    p_codex_agent.add_argument(
        "--name", help="Full instance name to use for this runner."
    )
    p_codex_agent.add_argument(
        "--suffix", help="Append this suffix to your main instance name."
    )
    p_codex_agent.add_argument(
        "--cwd", default=".", help="Directory to run Codex in."
    )
    p_codex_agent.add_argument(
        "--wait", type=int, default=90, help="Inbox poll wait (seconds)."
    )
    p_codex_agent.add_argument(
        "--announce", action="store_true", help="Send an online message when joining."
    )
    p_codex_agent.add_argument(
        "--no-launch", action="store_true", help="Run the sync loops but do not launch Codex."
    )
    p_codex_agent.add_argument(
        "--verbose", action="store_true", help="Print debug sync logs."
    )
    p_codex_agent.add_argument(
        "--sandbox", default="workspace-write", help="Codex sandbox level."
    )
    p_codex_agent.add_argument(
        "--approval", default="on-request", help="Codex approval mode."
    )
    p_codex_agent.add_argument(
        "--autonomous",
        action="store_true",
        help="Supervise autonomous codex exec runs triggered by new room messages.",
    )
    p_codex_agent.add_argument(
        "--room-poll",
        type=int,
        default=15,
        help="How often to refresh room state for autonomous triggers (seconds).",
    )
    p_codex_agent.add_argument(
        "--heartbeat", type=int, default=30, help="Presence heartbeat interval (seconds)."
    )
    p_codex_agent.add_argument(
        "--history-limit", type=int, default=25, help="Room history lines to sync."
    )"""

new_parser_gemini = old_parser.replace("codex", "gemini").replace("Codex", "Gemini")
if 'p_gemini_agent = sub.add_parser(' not in content:
    content = content.replace(old_parser, old_parser + "\n\n" + new_parser_gemini)

old_dict = '"codex-agent": _cmd_codex_agent,'
new_dict = '"codex-agent": _cmd_codex_agent,\n        "gemini-agent": _cmd_gemini_agent,'
if '"gemini-agent": _cmd_gemini_agent' not in content:
    content = content.replace(old_dict, new_dict)

open(path, "w").write(content)
print("cli.py patched successfully")
