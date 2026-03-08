import json
import subprocess
from pathlib import Path

CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


# ─── OpenClaw config helpers ──────────────────────────────────────────────────

def load_openclaw_config():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    # Remove any stale invalid channel keys from previous runs
    channels = config.get("channels", {})
    for bad_key in ["discord", "agentmail"]:
        if bad_key in channels:
            del channels[bad_key]
            print(f"🧹 Removed stale channels.{bad_key} from config")
    return config

def save_openclaw_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print("✅ OpenClaw config saved!")


# ─── Agent config loader ──────────────────────────────────────────────────────

def resolve_md_path(value: str, base_dir: Path) -> str:
    """If value is a path ending in .md, load and return its contents."""
    if not isinstance(value, str) or not value.strip().endswith(".md"):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    if path.exists():
        print(f"✅ Loaded: {path}")
        return path.read_text()
    print(f"⚠️  File not found, using raw value: {value}")
    return value

def load_agent_input(input_path: str) -> dict:
    input_path = Path(input_path).resolve()
    base_dir   = input_path.parent

    with open(input_path, "r") as f:
        data = json.load(f)

    # Resolve prompt_layers fields from .md paths if needed
    layers = data.setdefault("prompt_layers", {})
    if "game_instructions" in layers:
        layers["game_instructions"] = resolve_md_path(layers["game_instructions"], base_dir)
    if "system_prompt" in layers:
        layers["system_prompt"] = resolve_md_path(layers["system_prompt"], base_dir)
    layers["skills"] = [
        resolve_md_path(skill, base_dir)
        for skill in layers.get("skills", [])
    ]

    return data


# ─── Per-agent credential store ───────────────────────────────────────────────

def get_agent_auth_profiles_path(agent_id: str) -> Path:
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


# ─── Setup steps ─────────────────────────────────────────────────────────────

def setup_auth(config: dict, agent_input: dict) -> dict:
    """Write API keys to openclaw.json auth profiles + per-agent credential store."""
    credentials   = agent_input.get("credentials", {})
    agent_id      = agent_input.get("agent_id", "main")
    auth          = config.setdefault("auth", {})
    profiles      = auth.setdefault("profiles", {})
    cred_store    = load_agent_auth_profiles(agent_id)
    cred_profiles = cred_store.setdefault("profiles", {})

    openrouter_key = credentials.get("openrouter_api_key")
    if openrouter_key:
        profiles["openrouter:default"] = {"provider": "openrouter", "mode": "api_key"}
        cred_profiles["openrouter:default"] = {
            "type": "api_key", "provider": "openrouter", "key": openrouter_key
        }

    agentmail_key = credentials.get("agentmail_api_key")
    if agentmail_key:
        # AgentMail is configured via auth profile only.
        # OpenClaw auto-enables it — do NOT add it to channels.
        profiles["agentmail:default"] = {"provider": "agentmail", "mode": "api_key"}
        cred_profiles["agentmail:default"] = {
            "type": "api_key", "provider": "agentmail", "key": agentmail_key
        }
        print("✅ AgentMail configured (via auth profile — auto-enabled by OpenClaw)")

    save_agent_auth_profiles(agent_id, cred_store)
    print(f"✅ Auth credentials written to agent credential store (agent {agent_id})")
    return config

def setup_channels(config: dict, agent_input: dict) -> dict:
    """Configure Telegram channel in openclaw.json."""
    credentials      = agent_input.get("credentials", {})
    channels         = config.setdefault("channels", {})
    telegram_token   = credentials.get("telegram_bot_token")
    telegram_chat_id = credentials.get("telegram_group_chat_id")

    if telegram_token:
        telegram_cfg = {
            "enabled":     True,
            "botToken":    telegram_token,
            "dmPolicy":    "pairing",
            "groupPolicy": "open",
            "streaming":   "partial"
        }
        if telegram_chat_id:
            telegram_cfg["groupPolicy"]    = "allowlist"
            telegram_cfg["groupAllowFrom"] = [telegram_chat_id]

        channels["telegram"] = telegram_cfg
        chat_info = f" (group: {telegram_chat_id})" if telegram_chat_id else ""
        print(f"✅ Telegram channel configured{chat_info}")

    return config

def create_workspace(agent_id: str) -> Path:
    workspace = Path.home() / "clawd" / agent_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"✅ Workspace created at {workspace}")
    return workspace

def write_game_instructions(workspace: Path, content: str) -> None:
    if not content:
        print("⚠️  No game_instructions found, skipping")
        return
    out = workspace / "AGENTS.md"
    out.write_text(content.strip())
    print(f"✅ AGENTS.md written to {out}")

def write_skills(workspace: Path, skills: list) -> None:
    if not skills:
        print("⚠️  No skills found, skipping")
        return
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for i, skill_text in enumerate(skills, 1):
        skill_dir = skills_dir / f"skill-{i}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(skill_text.strip())
        print(f"✅ {skill_md}")
    print(f"✅ {len(skills)} skill(s) written to {skills_dir}")

def write_soul_md(workspace: Path, agent_input: dict) -> None:
    """Write SOUL.md — the user's system prompt."""
    system_prompt = agent_input.get("prompt_layers", {}).get("system_prompt", "")
    if not system_prompt:
        print("⚠️  No system_prompt found, skipping SOUL.md")
        return
    soul_md = workspace / "SOUL.md"
    soul_md.write_text(system_prompt.strip())
    print(f"✅ SOUL.md written to {soul_md}")

def setup_agent(config: dict, agent_input: dict, workspace: Path) -> dict:
    agent_id        = agent_input.get("agent_id", "main")
    agent_name      = agent_input.get("agent_name", "Agent")
    model           = agent_input.get("model", "anthropic/claude-sonnet-4-6")
    openclaw_native = agent_input.get("openclaw_native", {})

    agents     = config.setdefault("agents", {})
    agent_list = agents.setdefault("list", [])

    # Remove existing agent with same id before re-adding
    agents["list"] = [a for a in agent_list if a.get("id") != agent_id]

    new_agent = {
        "id":        agent_id,
        "name":      agent_name,
        "model":     {"primary": model},
        "workspace": str(workspace),
        # skills must be an array — systemPrompt is NOT valid here, it lives in SOUL.md
        "skills": [
            openclaw_native.get("wallet_skill", "agent-wallet-usdc")
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


# ─── Main ─────────────────────────────────────────────────────────────────────

def setup_openclaw_agent(input_path: str) -> None:
    print(f"🦞 Setting up OpenClaw agent from: {input_path}\n")

    agent_input = load_agent_input(input_path)
    config      = load_openclaw_config()
    agent_id    = agent_input.get("agent_id", "main")
    workspace   = create_workspace(agent_id)

    layers = agent_input.get("prompt_layers", {})

    # Write workspace files
    write_game_instructions(workspace, layers.get("game_instructions", ""))
    write_soul_md(workspace, agent_input)
    write_skills(workspace, layers.get("skills", []))

    # Update openclaw.json
    config = setup_auth(config, agent_input)
    config = setup_channels(config, agent_input)
    config = setup_agent(config, agent_input, workspace)
    config = setup_tools(config)

    save_openclaw_config(config)
    restart_gateway()

    # Identity summary
    credentials      = agent_input.get("credentials", {})
    game_api         = agent_input.get("game_api", {})
    agent_name       = agent_input.get("agent_name", "")
    agentmail_inbox  = credentials.get("agentmail_inbox_id", "")
    telegram_chat_id = credentials.get("telegram_group_chat_id", "")
    base_url         = game_api.get("base_url", "")

    print(f"\n🦞 Done! Agent '{agent_name}' is ready.")
    print("   Open your dashboard with: openclaw dashboard")
    print("\n" + "="*56)
    print("🪪  AGENT IDENTITY")
    print("="*56)
    print(f"  Agent Name          : {agent_name}")
    print(f"  Agent ID            : {agent_id}")
    print(f"  Lobby ID            : {agent_input.get('lobby_id', '')}")
    print(f"  Model               : {agent_input.get('model', '')}")
    print(f"  Agent Email         : {agentmail_inbox}")
    print(f"  Telegram Group Chat : {telegram_chat_id}")
    print(f"  Leaderboard         : {base_url}{game_api.get('leaderboard_path', '')}")
    print(f"  Game State          : {base_url}{game_api.get('game_state_path', '')}")
    print("="*56)


if __name__ == "__main__":
    import sys
    input_file = sys.argv[1] if len(sys.argv) > 1 else "agent_config.json"
    setup_openclaw_agent(input_file)
