import sys

content = open('gemini_agent.py').read()

old_func = """def _gemini_base_command(
    *,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
) -> list[str]:
    cmd = [
        "-C",
        str(cwd),
        "-c",
        f"mcp_servers.quorus.env.QUORUS_INSTANCE_NAME={json.dumps(participant)}",
        "-c",
        f"mcp_servers.quorus.env.QUORUS_RELAY_URL={json.dumps(relay_url)}",
    ]
    if api_key:
        cmd.extend(
            ["-c", f"mcp_servers.quorus.env.QUORUS_API_KEY={json.dumps(api_key)}"]
        )
    elif relay_secret:
        cmd.extend(
            [
                "-c",
                f"mcp_servers.quorus.env.QUORUS_RELAY_SECRET={json.dumps(relay_secret)}",
            ]
        )
    else:
        raise GeminiAgentError(
            "Gemini launch needs Quorus relay auth (workspace API key or relay secret)."
        )
    return cmd"""

new_func = """def _gemini_base_env(
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    import os
    env = os.environ.copy()
    env["QUORUS_INSTANCE_NAME"] = participant
    env["QUORUS_RELAY_URL"] = relay_url
    if api_key:
        env["QUORUS_API_KEY"] = api_key
    elif relay_secret:
        env["QUORUS_RELAY_SECRET"] = relay_secret
    else:
        raise GeminiAgentError(
            "Gemini launch needs Quorus relay auth (workspace API key or relay secret)."
        )
    return env"""

content = content.replace(old_func, new_func)

old_interactive = """def build_gemini_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    approval: str,
    inbox_path: Path,
    context_path: Path | None = None,
) -> list[str]:
    \"\"\"Build the interactive Gemini command line with Quorus overrides.\"\"\"
    prompt = build_prompt(room, participant, inbox_path, context_path)
    return [
        "gemini",
        *_gemini_base_command(
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
        ),
        "-s",
        sandbox,
        "-a",
        approval,
        prompt,
    ]"""

new_interactive = """def build_gemini_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    approval: str,
    inbox_path: Path,
    context_path: Path | None = None,
) -> tuple[list[str], dict]:
    \"\"\"Build the interactive Gemini command line with Quorus overrides.\"\"\"
    prompt = build_prompt(room, participant, inbox_path, context_path)
    env = _gemini_base_env(participant, relay_url, api_key, relay_secret)
    cmd = ["gemini", "--worktree", str(cwd), "--approval-mode", "default", "-i", prompt]
    return cmd, env"""

content = content.replace(old_interactive, new_interactive)

old_exec = """def build_gemini_exec_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    inbox_path: Path,
    context_path: Path,
    prompt: str,
) -> list[str]:
    \"\"\"Build a non-interactive Gemini command for supervised autonomous turns.\"\"\"
    return [
        "gemini",
        "exec",
        *_gemini_base_command(
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
        ),
        "-s",
        sandbox,
        "--skip-git-repo-check",
        build_prompt(room, participant, inbox_path, context_path) + "\\n\\n" + prompt,
    ]"""

new_exec = """def build_gemini_exec_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    inbox_path: Path,
    context_path: Path,
    prompt: str,
) -> tuple[list[str], dict]:
    \"\"\"Build a non-interactive Gemini command for supervised autonomous turns.\"\"\"
    env = _gemini_base_env(participant, relay_url, api_key, relay_secret)
    full_prompt = build_prompt(room, participant, inbox_path, context_path) + "\\n\\n" + prompt
    cmd = ["gemini", "--worktree", str(cwd), "--approval-mode", "yolo", "-p", full_prompt]
    return cmd, env"""

content = content.replace(old_exec, new_exec)

old_call_exec = """cmd = build_gemini_exec_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
            sandbox=sandbox,
            inbox_path=inbox_path,
            context_path=context_path,
            prompt=prompt,
        )
        rc = subprocess.call(cmd)"""

new_call_exec = """cmd, env = build_gemini_exec_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
            sandbox=sandbox,
            inbox_path=inbox_path,
            context_path=context_path,
            prompt=prompt,
        )
        rc = subprocess.call(cmd, env=env)"""

content = content.replace(old_call_exec, new_call_exec)

old_call_interactive = """cmd = build_gemini_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=agent_api_key,
            relay_secret=effective_secret,
            cwd=cwd,
            sandbox=sandbox,
            approval=approval,
            inbox_path=inbox_path,
            context_path=context_path,
        )
        return subprocess.call(cmd)"""

new_call_interactive = """cmd, env = build_gemini_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=agent_api_key,
            relay_secret=effective_secret,
            cwd=cwd,
            sandbox=sandbox,
            approval=approval,
            inbox_path=inbox_path,
            context_path=context_path,
        )
        return subprocess.call(cmd, env=env)"""

content = content.replace(old_call_interactive, new_call_interactive)

open('gemini_agent.py', 'w').write(content)
