import json
import shlex
import subprocess
from pathlib import Path

CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


# ─── OpenClaw config helpers ──────────────────────────────────────────────────

def load_openclaw_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"📝 No existing config found — starting fresh ({CONFIG_PATH})")
        return {}
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

def load_agent_input(input_path: str) -> dict:
    input_path = Path(input_path).resolve()

    with open(input_path, "r") as f:
        data = json.load(f)

    data.setdefault("prompt_layers", {})
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

    wallet_key = credentials.get("wallet_private_key")
    if wallet_key:
        profiles["wallet:default"] = {"provider": "wallet", "mode": "token"}
        cred_profiles["wallet:default"] = {
            "type": "private_key", "provider": "wallet", "key": wallet_key
        }
        print("✅ Wallet private key configured (for agent-wallet-usdc skill)")

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
            "streaming":   "partial",
        }
        if telegram_chat_id:
            telegram_cfg["groups"] = {
                str(telegram_chat_id): {"requireMention": False},
            }

        channels["telegram"] = telegram_cfg
        chat_info = f" (group: {telegram_chat_id})" if telegram_chat_id else ""
        print(f"✅ Telegram channel configured{chat_info}")

    return config

def create_workspace(agent_id: str) -> Path:
    workspace = Path.home() / "clawd" / agent_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"✅ Workspace created at {workspace}")
    return workspace


def write_workspace_env(workspace: Path, agent_input: dict) -> None:
    """Write .env in the agent workspace so agent-wallet-usdc skill can read WALLET_SEED_PHRASE and NETWORK."""
    credentials = agent_input.get("credentials", {})
    seed_phrase = credentials.get("wallet_seed_phrase", "").strip()
    if not seed_phrase:
        return
    network = credentials.get("network", "mainnet").strip() or "mainnet"
    env_path = workspace / ".env"
    lines = [
        f'WALLET_SEED_PHRASE="{seed_phrase}"',
        f"NETWORK={network}",
    ]
    env_path.write_text("\n".join(lines) + "\n")
    print(f"✅ Workspace .env written (WALLET_SEED_PHRASE + NETWORK={network}) for agent-wallet-usdc")

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

def ensure_openrouter_prefix(model: str) -> str:
    """Ensure model string is prefixed with 'openrouter/' so OpenClaw routes via OpenRouter."""
    if model.startswith("openrouter/"):
        return model
    return f"openrouter/{model}"

def setup_agent(config: dict, agent_input: dict, workspace: Path) -> dict:
    agent_id        = agent_input.get("agent_id", "main")
    agent_name      = agent_input.get("agent_name", "Agent")
    raw_model       = agent_input.get("model", "anthropic/claude-sonnet-4-6")
    model           = ensure_openrouter_prefix(raw_model)
    openclaw_native = agent_input.get("openclaw_native", {})

    agents     = config.setdefault("agents", {})
    agent_list = agents.setdefault("list", [])

    # Remove existing agent with same id before re-adding
    agents["list"] = [a for a in agent_list if a.get("id") != agent_id]

    new_agent = {
        "id":        agent_id,
        "name":      agent_name,
        "model":     model,
        "workspace": str(workspace),
        "skills": [
            openclaw_native.get("wallet_skill", "agent-wallet-usdc")
        ]
    }

    agents["list"].append(new_agent)
    print(f"✅ Agent '{agent_name}' (id: {agent_id}) configured with model {model}")
    return config

def setup_tools(config: dict) -> dict:
    config["tools"] = {
        "allow": ["agent-wallet-usdc"],
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

def setup_gateway(config: dict) -> dict:
    gw = config.setdefault("gateway", {})
    gw.setdefault("mode", "local")
    return config

def install_wallet_skill() -> None:
    """Install agent-wallet-usdc skill via clawhub (for sending USDC on Base)."""
    print("\n📦 Installing agent-wallet-usdc skill (for USDC on Base)...")
    # Pipe "y" to stdin to auto-accept VirusTotal/suspicious-skill warning (non-interactive)
    result = subprocess.run(
        ["npx", "clawhub", "install", "agent-wallet-usdc", "--force"],
        input=b"y\n",
        check=False,
    )
    if result.returncode == 0:
        print("✅ agent-wallet-usdc skill installed!")
    else:
        print(
            "⚠️  clawhub install failed — you can try manually: "
            "npx clawhub install agent-wallet-usdc --force"
        )


def install_wallet_skill_npm_deps(skill_dir: Path | None) -> None:
    """Run npm install in the agent-wallet-usdc skill directory if it exists."""
    if skill_dir is None or not skill_dir.is_dir():
        return
    package_json = skill_dir / "package.json"
    if not package_json.exists():
        return
    print("\n📦 Installing npm dependencies for agent-wallet-usdc...")
    result = subprocess.run(
        ["npm", "install"],
        cwd=skill_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print("✅ agent-wallet-usdc npm dependencies installed!")
    else:
        print(f"⚠️  npm install failed in {skill_dir}: {result.stderr or result.stdout}")


def restart_gateway() -> None:
    print("\n🔄 Starting gateway in background...")
    log_dir = Path.home() / ".openclaw" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "gateway.log"
    # Run gateway in foreground (as OpenClaw recommends when systemd unavailable),
    # but under nohup so it survives script exit. Use a single string so the shell
    # runs the full command; a list with shell=True would run only "nohup".
    cmd = f"nohup openclaw gateway >> {shlex.quote(str(log_file))} 2>&1 &"
    subprocess.run(
        cmd,
        shell=True,
        cwd=Path.home(),
        check=False,
    )
    # Brief pause so the process can bind before we exit
    import time
    time.sleep(2)
    print("✅ Gateway started in background (logs: {})".format(log_file))


# ─── Main ─────────────────────────────────────────────────────────────────────

def setup_openclaw_agent(input_path: str) -> None:
    print(f"🦞 Setting up OpenClaw agent from: {input_path}\n")

    agent_input = load_agent_input(input_path)
    config      = load_openclaw_config()
    agent_id    = agent_input.get("agent_id", "main")
    workspace   = create_workspace(agent_id)

    write_workspace_env(workspace, agent_input)

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
    config = setup_gateway(config)

    save_openclaw_config(config)
    install_wallet_skill()
    # Install npm deps for in-repo wallet skill so scripts can run
    wallet_skill_dir = Path(__file__).resolve().parent / "skills" / "agent-wallet-usdc"
    install_wallet_skill_npm_deps(wallet_skill_dir)
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
    if len(sys.argv) != 2:
        print("Usage: python setup_agent.py <config.json>")
        sys.exit(1)
    setup_openclaw_agent(sys.argv[1])
