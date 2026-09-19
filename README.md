# Angel Tiers

A small Discord bot that looks up public [SubTiers](https://subtiers.net) profiles and makes cached gamemode tier-distribution graphs.

## Commands

- `/tier <Minecraft IGN or UUID>` — player profile, overall position, points, region, Minecraft head, and tiers.
- `/get-user <Discord user>` — the Minecraft account connected to that Discord account, if one exists.
- `/graph <gamemode> [update]` — counts every ranked player by HT/LT tier in a gamemode. Graphs are cached for one hour; set `update: True` to refresh now.

## Run it

1. Install Python 3.10+ and run `python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, then set `DISCORD_TOKEN` to a freshly generated Discord bot token. Never commit this file.
3. In the Discord Developer Portal, invite the bot with the `bot` and `applications.commands` scopes.
4. Run `python bot.py`.

## DMs

All slash commands are registered for servers, DMs, and group DMs. In the Discord Developer Portal under **Installation**, enable **User Install** and add the `applications.commands` scope. Users can then install the app to their account and use its slash commands in a DM with the bot.

Set `DEV_GUILD_ID` in `.env` while developing to make command changes available immediately in one server. Without it, global command changes can take a little while to appear.

The bot uses the public SubTiers v2 API and Mineatar's player-head endpoint. Cached graph images are written to `data/cache/` and are intentionally ignored by Git.
