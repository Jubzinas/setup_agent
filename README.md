# setup_agent

A Python script that provisions an [OpenClaw](https://openclaw.ai) agent from a single JSON config file. It reads credentials, prompt layers, and metadata from a portable `config.json`, then writes everything into the correct locations so the OpenClaw gateway can run the agent.

## Quick start

```bash
python setup_agent.py config.json
```

A config file path is required — the script will exit with an error if none is provided.

## How it works

`setup_agent.py` performs six steps, in order:

### 1. Load the input config

The script reads the JSON file passed as its argument (e.g. `config.json`). All `prompt_layers` values — `game_instructions`, `system_prompt`, and each entry in `skills` — are expected to be inline strings directly in the JSON. No external file resolution is performed.

### 2. Create the agent workspace

A workspace directory is created at `~/clawd/<agent_id>/`. This is where OpenClaw looks for the agent's runtime files.

### 3. Write workspace files from `prompt_layers`

Three files may be written into the workspace, all sourced from the `prompt_layers` object in the config:

| Config field | Destination file | Purpose |
|---|---|---|
| `prompt_layers.game_instructions` | `~/clawd/<agent_id>/AGENTS.md` | Top-level instructions the agent follows during the game |
| `prompt_layers.system_prompt` | `~/clawd/<agent_id>/SOUL.md` | The agent's system prompt / persona |
| `prompt_layers.skills[]` | `~/clawd/<agent_id>/skills/skill-N/SKILL.md` | One file per skill, each in its own numbered directory |

Each skill string should include a YAML frontmatter header so OpenClaw can index it properly:

```
---
name: my-skill-slug
description: One-sentence description. Use when [trigger].
---

# Skill Title

Skill body in Markdown…
```

### 4. Configure authentication (`credentials` → `~/.openclaw/openclaw.json` + per-agent credential store)

API keys from the `credentials` object are written to **two** places:

- **`~/.openclaw/openclaw.json`** — the global OpenClaw config gets an auth *profile entry* for each provider (e.g. `openrouter:default`, `agentmail:default`). These entries declare *which* providers the agent uses and their auth mode, but do **not** contain the secret keys themselves.
- **`~/.openclaw/agents/<agent_id>/agent/auth-profiles.json`** — the per-agent credential store where the actual secret keys are persisted.

The mapping from config fields to auth profiles:

| `credentials` field | Auth profile key | Notes |
|---|---|---|
| `openrouter_api_key` | `openrouter:default` | Used for LLM inference via OpenRouter |
| `wallet_private_key` | `wallet:default` | Hex private key for the agent wallet (agent-wallet-usdc) |
| `wallet_seed_phrase` | — | If set, written to `~/clawd/<agent_id>/.env` as `WALLET_SEED_PHRASE` so the agent-wallet-usdc skill can sign transfers (use 12/24-word BIP-39 mnemonic) |
| `network` | — | Written to workspace `.env` as `NETWORK` (e.g. `mainnet` or `testnet`); default `mainnet` |
| `agentmail_api_key` | `agentmail:default` | OpenClaw auto-enables the AgentMail channel when this profile exists — no explicit channel entry needed |

### 5. Configure channels (`credentials` → `~/.openclaw/openclaw.json`)

Channel configuration is written to the `channels` section of the global OpenClaw config. Currently only Telegram is configured as an explicit channel:

| `credentials` field | Channel key | Behaviour |
|---|---|---|
| `telegram_bot_token` | `channels.telegram` | Enables the Telegram bot with DM pairing and open group policy |
| `telegram_group_chat_id` | (same) | If provided, switches group policy to `allowlist` restricted to that chat ID |

AgentMail is **not** added to `channels` — OpenClaw detects the `agentmail:default` auth profile and enables the channel automatically. Any stale `channels.discord` or `channels.agentmail` keys left over from previous runs are cleaned up on config load.

### 6. Register the agent and configure tools

The agent entry is added (or replaced) in `agents.list` inside `openclaw.json`:

```json
{
  "id": "<agent_id>",
  "name": "<agent_name>",
  "model": { "primary": "<model>" },
  "workspace": "~/clawd/<agent_id>",
  "skills": ["<openclaw_native.wallet_skill>"]
}
```

The `openclaw_native.wallet_skill` value from the config (e.g. `agent-wallet-usdc`) is included in the agent's built-in skills array so it can send on-chain payments.

A default `tools` block is also written enabling elevated tool access from webchat and Telegram, plus web search and fetch capabilities.

Finally the script runs `openclaw doctor --fix`, `openclaw gateway install`, and `openclaw gateway start` to restart the gateway with the new configuration.

## Files touched at runtime

| Path | What |
|---|---|
| `~/.openclaw/openclaw.json` | Global OpenClaw config (auth profiles, channels, agents, tools) |
| `~/.openclaw/agents/<agent_id>/agent/auth-profiles.json` | Per-agent secret credential store |
| `~/clawd/<agent_id>/.env` | `WALLET_SEED_PHRASE` and `NETWORK` (when `credentials.wallet_seed_phrase` is set) — used by agent-wallet-usdc |
| `~/clawd/<agent_id>/AGENTS.md` | Game instructions |
| `~/clawd/<agent_id>/SOUL.md` | System prompt |
| `~/clawd/<agent_id>/skills/skill-N/SKILL.md` | Individual skill files |