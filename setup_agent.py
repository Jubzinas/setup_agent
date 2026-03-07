import json
import os
import subprocess
from pathlib import Path

CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"

def load_openclaw_config():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    # Clean up any stale invalid keys from previous runs
    channels = config.get("channels", {})
    for bad_key in ["agentmail", "discord"]:
        if bad_key in channels:
            del channels[bad_key]
            print(f"🧹 Removed stale channels.{bad_key} from config")
    return config

def save_openclaw_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print("✅ OpenClaw config saved!")

def resolve_md_path(value: str, base_dir: Path) -> str:
    """Resolve a value to file content if it's a .md path (absolute or relative to base_dir)."""
    if not isinstance(value, str) or not value.strip().endswith(".md"):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    if path.exists():
        print(f"✅ Loaded: {path}")
        return path.read_text()
    else:
        print(f"⚠️  File not found, using raw value: {value}")
        return value

def load_agent_input(input_path: str) -> dict:
    input_path = Path(input_path).resolve()
    base_dir   = input_path.parent

    with open(input_path, "r") as f:
        data = json.load(f)

    # Resolve game_instructions
    if "game_instructions" in data:
        data["game_instructions"] = resolve_md_path(data["game_instructions"], base_dir)

    # Resolve system_prompt
    if "system_prompt" in data:
        data["system_prompt"] = resolve_md_path(data["system_prompt"], base_dir)

    # Resolve each skill
    data["skills"] = [
        resolve_md_path(skill, base_dir)
        for skill in data.get("skills", [])
    ]

    return data

def get_agent_auth_profiles_path(agent_id: str) -> Path:
    """Per-agent credential store: ~/.openclaw/agents/{id}/agent/auth-profiles.json"""
    return Path.home() / ".openclaw" / "agents" / agent_id / "agent" / "auth-profiles.json"

def load_agent_auth_profiles(agent_id: str) -> dict:
    path = get_agent_auth_profiles_path(agent_id)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"version": 1, "profiles": {}}

def save_agent_auth_profiles(agent_id: str, data: dict) -> None:
    path = get_agent_auth_profiles_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def setup_auth(config: dict, agent_input: dict) -> dict:
    auth     = config.setdefault("auth", {})
    profiles = auth.setdefault("profiles", {})

    agent_id      = agent_input.get("agent_id", "main")
    cred_store    = load_agent_auth_profiles(agent_id)
    cred_profiles = cred_store.setdefault("profiles", {})

    openrouter_key = agent_input.get("openrouter_api_key")
    if openrouter_key:
        profiles["openrouter:default"] = {
            "provider": "openrouter",
            "mode": "api_key"
        }
        cred_profiles["openrouter:default"] = {
            "type": "api_key",
            "provider": "openrouter",
            "key": openrouter_key
        }

    openclaw_cfg  = agent_input.get("openclaw_config", {})
    agentmail_key = openclaw_cfg.get("agentmail_api_key")
    if agentmail_key:
        profiles["agentmail:default"] = {
            "provider": "agentmail",
            "mode": "api_key"
        }
        cred_profiles["agentmail:default"] = {
            "type": "api_key",
            "provider": "agentmail",
            "key": agentmail_key
        }

    save_agent_auth_profiles(agent_id, cred_store)
    print(f"✅ Auth credentials written to agent credential store (agent {agent_id})")

    return config

def setup_channels(config: dict, agent_input: dict) -> dict:
    openclaw_cfg = agent_input.get("openclaw_config", {})
    channels     = config.setdefault("channels", {})

    telegram_token = openclaw_cfg.get("telegram_api")
    if telegram_token:
        channels["telegram"] = {
            "enabled":     True,
            "botToken":    telegram_token,
            "dmPolicy":    "pairing",
            "groupPolicy": "open",
            "streaming":   "partial"
        }
        print("✅ Telegram channel configured")

    # NOTE: agentmail is NOT a valid channels key — OpenClaw auto-configures it.
    # The agentmail auth profile (set in setup_auth) is sufficient.
    agentmail_inbox = openclaw_cfg.get("agentmail_inbox_id")
    if agentmail_inbox:
        print("✅ AgentMail configured (via auth profile)")

    return config

def create_workspace(agent_id: str) -> Path:
    workspace = Path.home() / "clawd" / agent_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"✅ Workspace created at {workspace}")
    return workspace

def write_game_instructions(workspace: Path, content: str) -> None:
    """Write the fixed server-controlled game instructions to workspace."""
    if not content:
        print("⚠️  No game_instructions found, skipping")
        return
    out = workspace / "GAME_INSTRUCTIONS.md"
    out.write_text(content.strip())
    print(f"✅ GAME_INSTRUCTIONS.md written to {out}")

def write_skills_file(workspace: Path, skills: list[str]) -> None:
    """Concatenate all resolved skill strings into SKILLS.md."""
    if not skills:
        print("⚠️  No skills found, skipping")
        return
    parts = []
    for i, skill_text in enumerate(skills, 1):
        parts.append(f"\n\n---\n# Skill {i}\n\n{skill_text}")
    skills_file = workspace / "SKILLS.md"
    skills_file.write_text("\n".join(parts).strip())
    print(f"✅ SKILLS.md written to {skills_file} ({len(skills)} skills)")

def write_soul_md(workspace: Path, agent_input: dict) -> None:
    """Write SOUL.md — agent identity combining game instructions + system prompt."""
    agent_name    = agent_input.get("agent_name", "Agent")
    agent_id      = agent_input.get("agent_id", "")
    system_prompt = agent_input.get("system_prompt", "")
    game_instr    = agent_input.get("game_instructions", "")
    wallet_key    = agent_input.get("wallet_private_key", "")
    wallet_addr   = agent_input.get("wallet_address", "")
    openclaw_cfg  = agent_input.get("openclaw_config", {})
    game_cfg      = agent_input.get("game_api_config", {})
    agent_email   = openclaw_cfg.get("agentmail_inbox_id", f"{agent_name.lower().replace(' ', '-')}@agentmail.to")

    content = f"""# {agent_name}

## Identity
- Agent Name     : {agent_name}
- Agent ID       : {agent_id}
- Agent Email    : {agent_email}
- Wallet Address : {wallet_addr}
- Lobby Name     : {game_cfg.get("lobby_name", "")}
- Lobby ID       : {game_cfg.get("lobby_id", "")}
- Kill Interval  : {game_cfg.get("kill_interval_seconds", "")}s

---

## Game Instructions (Server-Controlled — Highest Priority)
{game_instr}

---

## System Prompt (User-Defined)
{system_prompt}

## Wallet
- Chain: {openclaw_cfg.get("wallet_chain", "base")}
- Skill: {openclaw_cfg.get("wallet_skill", "agent-wallet-usdc")}
- Private Key: {wallet_key}

## Game API
- Leaderboard URL: {game_cfg.get("leaderboard_url", "")}
- Game State URL:  {game_cfg.get("game_state_url", "")}
"""
    soul_md = workspace / "SOUL.md"
    soul_md.write_text(content.strip())
    print(f"✅ SOUL.md written to {soul_md}")

def setup_agent(config: dict, agent_input: dict, workspace: Path) -> dict:
    agent_id     = agent_input.get("agent_id", "main")
    agent_name   = agent_input.get("agent_name", "Agent")
    model        = agent_input.get("model", "anthropic/claude-sonnet-4-6")
    openclaw_cfg = agent_input.get("openclaw_config", {})

    agents     = config.setdefault("agents", {})
    agent_list = agents.setdefault("list", [])

    # Remove existing agent with same id
    agents["list"] = [a for a in agent_list if a.get("id") != agent_id]

    new_agent = {
        "id":        agent_id,
        "name":      agent_name,
        "model":     {"primary": model},
        "workspace": str(workspace),
        # skills must be an array of skill name strings (not an object)
        # systemPrompt is NOT a valid field here — prompt lives in SOUL.md (workspace)
        "skills": [
            openclaw_cfg.get("wallet_skill", "agent-wallet-usdc")
        ]
    }

    agents["list"].append(new_agent)
    print(f"✅ Agent '{agent_name}' (id: {agent_id}) configured")
    return config

def setup_tools(config: dict) -> dict:
    config["tools"] = {
        "elevated": {
            "enabled": True,
            "allowFrom": {
                "webchat":  ["webchat"],
                "telegram": ["telegram"]
            }
        },
        "web": {
            "search": {"enabled": True},
            "fetch":  {"enabled": True}
        }
    }
    return config

def restart_gateway() -> None:
    print("\n🔄 Restarting gateway...")
    subprocess.run(["openclaw", "doctor", "--fix"], check=False)
    subprocess.run(["openclaw", "gateway", "install"], check=False)
    subprocess.run(["openclaw", "gateway", "start"],   check=False)
    print("✅ Gateway restarted!")

def setup_openclaw_agent(input_path: str) -> None:
    print(f"🦞 Setting up OpenClaw agent from: {input_path}\n")

    agent_input = load_agent_input(input_path)
    config      = load_openclaw_config()
    agent_id    = agent_input.get("agent_id", "main")
    workspace   = create_workspace(agent_id)

    # Write workspace files
    write_game_instructions(workspace, agent_input.get("game_instructions", ""))
    write_soul_md(workspace, agent_input)
    write_skills_file(workspace, agent_input.get("skills", []))

    # Update openclaw.json
    config = setup_auth(config, agent_input)
    config = setup_channels(config, agent_input)
    config = setup_agent(config, agent_input, workspace)
    config = setup_tools(config)

    save_openclaw_config(config)
    restart_gateway()

    # Identity summary
    game_cfg      = agent_input.get("game_api_config", {})
    openclaw_cfg  = agent_input.get("openclaw_config", {})
    agent_name    = agent_input.get("agent_name", "")
    wallet_addr   = agent_input.get("wallet_address", "")
    agent_email   = openclaw_cfg.get("agentmail_inbox_id", f"{agent_name.lower().replace(' ', '-')}@agentmail.to")
    lobby_name    = game_cfg.get("lobby_name", "")
    lobby_id      = game_cfg.get("lobby_id", "")
    kill_interval = game_cfg.get("kill_interval_seconds", "")

    print(f"\n🦞 Done! Agent '{agent_name}' is ready.")
    print("   Open your dashboard with: openclaw dashboard")
    print("\n" + "="*52)
    print("🪪  AGENT IDENTITY — save these into your .md files")
    print("="*52)
    print(f"  Agent Name       : {agent_name}")
    print(f"  Wallet Address   : {wallet_addr}")
    print(f"  Agent Email      : {agent_email}")
    print(f"  Lobby Name       : {lobby_name}")
    print(f"  Lobby ID         : {lobby_id}")
    print(f"  Agent ID         : {agent_input.get('agent_id', '')}")
    print(f"  Kill Interval    : {kill_interval}s")
    print("="*52)

if __name__ == "__main__":
    import sys
    input_file = sys.argv[1] if len(sys.argv) > 1 else "agent_config.json"
    setup_openclaw_agent(input_file)