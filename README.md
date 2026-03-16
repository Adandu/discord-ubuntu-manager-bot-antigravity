# Discord Ubuntu Manager Bot

Manage remote Ubuntu servers via SSH and Discord Slash Commands. Run it as a Docker container and control your infrastructure directly from Discord.

---

## 🚀 Features

- **Multi-Server Support:** Manage an unlimited number of Ubuntu servers from a single bot.
- **Discord Autocomplete:** Seamlessly switch between servers in Discord using server aliases.
- **SSH Support:** Supports both **SSH Keys** and **Password-based** authentication.
- **Secure by Design:** No sensitive data is stored in the config; all secrets are passed via Environment Variables.
- **Slash Commands:**
  - `/update`: Run `apt update` and `apt upgrade` remotely.
  - `/process`: Search for running processes by name.
  - `/service`: Start, Stop, Restart, or check the Status of any systemd service.
  - `/logs`: Tail the last N lines of any log file.
  - `/disk`: Check disk space usage (`df -h`).

---

## 🛠️ Discord Bot Setup (Step-by-Step)

To use this bot, you must first create a Discord Application and get a Bot Token:

1.  **Go to the [Discord Developer Portal](https://discord.com/developers/applications).**
2.  Click **"New Application"** and give it a name (e.g., `Ubuntu Server Manager`).
3.  Go to the **"Bot"** tab on the left sidebar.
4.  Click **"Reset Token"** (if needed) and **Copy the Token**. Save this for your `.env` file (`DISCORD_TOKEN`).
5.  (Optional but recommended) Under **"Privileged Gateway Intents"**, enable **"Server Members Intent"** and **"Message Content Intent"**.
6.  Go to the **"OAuth2"** tab, then **"URL Generator"**.
7.  Select the following scopes:
    - `bot`
    - `applications.commands` (Crucial for Slash Commands)
8.  Select the following permissions:
    - `Send Messages`
    - `Use Slash Commands`
    - `Read Message History`
9.  **Copy the generated URL** and paste it into your browser to invite the bot to your server.

---

## 📦 Setup & Deployment

### 1. Requirements
- A Discord Bot Token (from the steps above).
- Docker and Docker Compose installed.
- Remote Ubuntu servers with SSH access.

### 2. Configuration
Clone the repository and copy the `.env.example` file to `.env`:
```bash
cp .env.example .env
```
Edit the `.env` file with your configuration:
- `DISCORD_TOKEN`: Your bot token.
- `GUILD_ID`: (Optional) Your Discord Server ID for faster command syncing.
- `SERVERS_JSON`: A JSON array of your servers.
- `SSH_KEY_...` or `SSH_PASS_...`: The actual secrets for each server.

**Example `SERVERS_JSON`:**
```json
[
  {
    "alias": "web-01",
    "host": "1.2.3.4",
    "user": "ubuntu",
    "auth_method": "key",
    "secret_env": "SSH_KEY_WEB01"
  }
]
```

### 3. Run with Docker
```bash
docker-compose up -d
```

---

## 🔒 Security Recommendations
- **Dedicated User:** Create a dedicated user on your Ubuntu servers for the bot (e.g., `discord-bot`).
- **Sudo Access:** To use `/update` or `/service`, ensure the user has `sudo` permissions without a password.
  - **Edit sudoers:** `sudo visudo`
  - **Add line:** `discord-bot ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/systemctl, /usr/bin/tail, /usr/bin/df`
- **SSH Keys:** Always prefer SSH Keys over passwords for better security.

---

## 📜 License
MIT License. Feel free to use and contribute!
