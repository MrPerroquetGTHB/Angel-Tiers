# Angel Tiers

A small Discord bot for players to get information about other players in [SubTiers](https://subtiers.net)

- `/tier <Minecraft IGN or UUID>` — player profile, overall position, points, region and tiers.
- `/compare <player1> <player2>` — side-by-side tier, point, rank, and region comparison.
- `/random` — show a random player from the top 100 overall-ranked players.
- `/pointvalue` — point values for every SubTiers tier.
- `/get-user <Discord user>` — the Minecraft account connected to that Discord account, if one exists.
- `/activelb [username]` — active leaderboard in pages of 10 with tier-card buttons, or one player's active-only tiers and active rank.
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
