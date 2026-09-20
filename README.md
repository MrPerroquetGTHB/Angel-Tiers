# Angel Tiers

A small Discord bot for players to get information about other players in [SubTiers](https://subtiers.net)

- `/tier <Minecraft IGN or UUID>` — player profile, overall position, points, region and tiers.
- `/compare <player1> <player2>` — side-by-side tier, point, rank, and region comparison.
- `/random` — show a random player from the top 100 overall-ranked players.
- `/pointvalue` — point values for every SubTiers tier.
- `/get-user <Discord user>` — the Minecraft account connected to that Discord account, if one exists.
- `/activelb [username]` — active leaderboard in pages of 10 with tier-card buttons, or one player's active-only tiers and active rank.
- `/graph <gamemode> [update]` — counts every ranked player by HT/LT tier in a gamemode. Graphs are cached for one hour; set `update: True` to refresh now.
