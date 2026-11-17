<p align="center">
  <img src="banner.png" alt="Diddy banner" width="100%">
</p>

<p align="center">
  <img src="logo.png" alt="Diddy logo" width="150">
</p>

<h3 align="center">Diddy, a Discord bot that tracks streaks and manages warnings.</h3>

<p align="center"><em>Built in Python. Streaks are tracked in your local timezone.</em></p>

<p align="center">
  <a href="#getting-started"><img src="https://img.shields.io/badge/GET%20STARTED-FFFFFF?style=for-the-badge&labelColor=000000" alt="Get Started"></a>
  <a href="#features"><img src="https://img.shields.io/badge/FEATURES-000000?style=for-the-badge&labelColor=FFFFFF" alt="Features"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-444444?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-000000?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Discord-Bot-000000?style=flat-square&logo=discord&logoColor=white" alt="Discord Bot">
  <img src="https://img.shields.io/badge/Database-SQLite-444444?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/Config-.env-FFFFFF?style=flat-square&labelColor=000000" alt=".env Config">
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#getting-started">Getting Started</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#tech-stack">Tech Stack</a>
</p>

---

## Features

- Streak tracking for users, using local timezone dates
- Warning system stored in a local SQLite database (`warnings.db`)
- [Add any other commands or features Diddy has]

## Getting Started

```bash
git clone https://github.com/[your-username]/diddy.git
cd diddy
pip install -r requirements.txt
```

Then run the bot:

```bash
python main.py
```

## Configuration

Create a `.env` file in the project root with your bot token. Do not commit this file.

```env
DISCORD_TOKEN=your_token_here
```

## Tech Stack

- Python
- discord.py
- SQLite
- python-dotenv

## License

This project is licensed under the [LICENSE_NAME] license. See [LICENSE](LICENSE) for details.
