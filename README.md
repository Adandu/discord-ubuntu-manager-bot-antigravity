# <img src="logo_hybrid.svg" width="48" height="48" valign="middle"> DiscoBunty

Manage remote Ubuntu servers via SSH and Discord Slash Commands. Run it as a Docker container and control your infrastructure directly from Discord.

---

## ⚠️ Disclaimer

**This bot was created 100% using Gemini Code.**  
Whoever wants to use this bot, they do so at their own risk. The authors and creators are not responsible for any damage, data loss, or security breaches resulting from the use of this software. Always review the code and test in a safe environment before deploying to production.

---

## 🚀 Features

- **Multi-Server Support:** Manage an unlimited number of Ubuntu servers from a single bot.
- **Easy Configuration:** Configure servers using simple numbered environment variables.
- **Discord Autocomplete:** Seamlessly switch between servers in Discord using server aliases.
- **Secure SSH Management:** Supports **SSH Keys** (via raw string or volume mount) and **Passwords**.
- **Real-time Logging:** Logs commands and errors instantly to Docker logs (Unbuffered).
- **Slash Commands:**
  - `/update`: Run `apt update` and `apt upgrade` remotely.
  - `/process`: Search for running processes by name.
  - `/service`: Start, Stop, Restart, or check the Status of any systemd service.
  - `/logs`: Tail the last N lines of any log file.
  - `/disk`: Check disk space usage (`df -h`).
  - `/docker ps`: List all containers (with optional `/docker` group enabled).
  - `/docker control`: Start, Stop, or Restart a specific container.
  - `/docker logs`: View the last N lines of container logs.
  - `/docker details`: View container image, internal IP, and port mappings.

---

## 🛠️ Discord Bot Setup (Step-by-Step)

To use this bot, you must first create a Discord Application and get a Bot Token:

### 1. Create the Bot & Get Token
1.  **Go to the [Discord Developer Portal](https://discord.com/developers/applications).**
2.  Click **"New Application"** and give it a name (e.g., `DiscoBunty`).
3.  Go to the **"Bot"** tab on the left sidebar.
4.  Click **"Reset Token"** (if needed) and **Copy the Token**. Save this for your `.env` file (`DISCORD_TOKEN`).
5.  (Optional but recommended) Under **"Privileged Gateway Intents"**, enable **"Server Members Intent"** and **"Message Content Intent"**.

### 2. Get your Guild ID (Server ID)
1.  Open your Discord client.
2.  Go to **User Settings** (the gear icon at the bottom left).
3.  Go to **Advanced** (under App Settings).
4.  Enable **Developer Mode**.
5.  Now, **right-click** on your server icon/name in the server list on the left.
6.  Click **"Copy Server ID"**. Save this for your `.env` file (`GUILD_ID`).

### 3. Invite the Bot
1.  Go to the **"OAuth2"** tab, then **"URL Generator"**.
2.  Select the following scopes:
    - `bot`
    - `applications.commands` (Crucial for Slash Commands)
3.  Select the following permissions:
    - `Send Messages`
    - `Use Slash Commands`
    - `Read Message History`
4.  **Copy the generated URL** and paste it into your browser to invite the bot to your server.

---

## 📦 Setup & Deployment

### 1. Requirements
- A Discord Bot Token & Guild ID (from the steps above).
- Docker and Docker Compose installed.
- Remote Ubuntu servers with SSH access.

### 2. Configuration
Clone the repository and copy the `.env.example` file to `.env`:
```bash
cp .env.example .env
```
Edit the `.env` file with your configuration. Servers are defined using a numbered format (`_1`, `_2`, ...):

- `DISCORD_TOKEN`: Your bot token.
- `GUILD_ID`: Your Discord Server ID for command syncing.
- `ENABLE_DOCKER`: Set to `true` to enable the `/docker` command group.
- `DISCORD_UBUNTU_SERVER_ALIAS_N`: The nickname for server N.
- `DISCORD_UBUNTU_SERVER_IP_N`: Hostname or IP.
- `DISCORD_UBUNTU_SERVER_AUTH_METHOD_N`: `key` or `password`.
- `DISCORD_UBUNTU_SERVER_KEY_N`: Raw SSH Key string OR path to key file (see below).
- `DISCORD_UBUNTU_SERVER_PASSWORD_N`: Server password (if method is password).

### 3. SSH Key Management
DiscoBunty supports two ways to handle SSH keys:

#### A. Volume Bind Mount (Recommended)
Place your `.key` files in a local folder named `./ssh_keys` and mount it in `docker-compose.yml`. Then point the variable to the path inside the container:
```bash
DISCORD_UBUNTU_SERVER_KEY_1=/app/ssh_keys/masterchief.key
```

#### B. Raw Environment Variable
Paste the entire content of your private key directly into the `.env` file (ensure you handle newlines correctly):
```bash
DISCORD_UBUNTU_SERVER_KEY_1="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

### 4. Run with Docker
```bash
docker-compose up -d
```

---

## 🔒 Security Recommendations
- **Dedicated User:** Create a dedicated user on your Ubuntu servers for the bot (e.g., `discobunty`).
- **Sudo Access:** To use `/update`, `/service`, or `/docker`, ensure the user has `sudo` permissions without a password.
  - **Edit sudoers:** `sudo visudo`
  - **Add line:** `discobunty ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/systemctl, /usr/bin/tail, /usr/bin/df, /usr/bin/docker`
- **SSH Keys:** Always prefer SSH Keys over passwords for better security.

---

## 📜 License
MIT License. Feel free to use and contribute!
