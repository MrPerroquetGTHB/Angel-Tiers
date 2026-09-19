"""Angel Tiers: a Discord bot for the public SubTiers v2 API."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


API_BASE = "https://subtiers.net/api/v2"
MINEATAR_HEAD = "https://api.mineatar.io/head/"
EMBED_COLOUR = discord.Colour.from_rgb(135, 206, 250)  # light sky blue
CACHE_SECONDS = 60 * 60
CACHE_DIR = Path("data/cache")
GRAPH_STYLE_VERSION = "v4"
ACTIVE_LEADERBOARD_CACHE = CACHE_DIR / "active-leaderboard-v1.json"
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")

# This is also the ordering used by SubTiers' own UI.
MODE_LABELS = {
    "minecart": "Minecart", "dia_crystal": "Diamond Vanilla", "dia_smp": "Diamond SMP",
    "bed": "Bed", "bow": "Bow", "speed": "Speed", "creeper": "Creeper",
    "og_vanilla": "OG Vanilla", "debuff": "DeBuff", "elytra": "Elytra",
    "manhunt": "Manhunt", "trident": "Trident",
}
# Custom emojis supplied for the Angel Tiers Discord server.
MODE_EMOJIS = {
    "creeper": "<:creeper:1550997830804447322>",
    "speed": "<:speed:1550997829609332736>",
    "og_vanilla": "<:ogv:1550997828296245400>",
    "elytra": "<:elytra:1550997826895618099>",
    "manhunt": "<:manhunt:1550997823623794799>",
    "debuff": "<:debuff:1550997822063648768>",
    "trident": "<:trident:1550997821023461426>",
    "bed": "<:bed:1550997819760975973>",
    "dia_smp": "<:d_smp:1550997818578051142>",
    "minecart": "<:cart:1550997817030611055>",
    "dia_crystal": "<:d_crystal:1550997816195940514>",
    "bow": "<:bow:1550997814757040259>",
}
TIER_EMOJIS = {
    "LT1": "<:lt1:1550998547120394351>",
    "LT2": "<:lt2:1550998540690653274>",
    "LT3": "<:lt3:1550998545908244500>",
    "HT1": "<:ht1:1550998541994950706>",
    "HT2": "<:ht2:1550998543471476876>",
    "HT3": "<:ht3:155099854238772316>",
}
TIER_ORDER = ("HT1", "LT1", "HT2", "LT2", "HT3", "LT3", "HT4", "LT4", "HT5", "LT5")
TIER_POINTS = {"HT1": 60, "LT1": 45, "HT2": 30, "LT2": 20, "HT3": 10, "LT3": 6, "HT4": 4, "LT4": 3, "HT5": 2, "LT5": 1}


class SubtiersAPIError(Exception):
    """A public-facing API error that can be safely shown to a Discord user."""


class SubtiersClient:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Angel-Tiers Discord Bot/1.0"},
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def get(self, path: str) -> Any:
        if not self.session:
            raise RuntimeError("HTTP session is not ready")
        try:
            # The public API permits 120 requests per minute. Large graphs can
            # legitimately need more than that because its pagination is capped
            # at 50 players per tier, so honour its Retry-After response.
            for attempt in range(4):
                async with self.session.get(f"{API_BASE}/{path.lstrip('/')}") as response:
                    if response.status == 404:
                        raise SubtiersAPIError("No linked SubTiers account was found.")
                    if response.status != 429:
                        if response.status >= 400:
                            raise SubtiersAPIError("SubTiers could not process that request right now.")
                        return await response.json()
                    retry_after = float(response.headers.get("Retry-After", "15"))
                if attempt == 3:
                    break
                await asyncio.sleep(min(max(retry_after, 1), 70))
            raise SubtiersAPIError("SubTiers is rate-limiting requests. Please try again shortly.")
        except asyncio.TimeoutError as error:
            raise SubtiersAPIError("SubTiers did not respond in time. Please try again.") from error
        except aiohttp.ClientError as error:
            raise SubtiersAPIError("Could not reach SubTiers. Please try again later.") from error

    async def profile(self, identifier: str) -> dict[str, Any]:
        clean = identifier.strip()
        path = f"profile/{clean}" if UUID_PATTERN.fullmatch(clean) else f"profile/by-name/{clean}"
        payload = await self.get(path)
        if not isinstance(payload, dict) or "uuid" not in payload:
            raise SubtiersAPIError("SubTiers returned an unexpected player profile.")
        return payload

    async def profile_by_discord(self, discord_id: int) -> dict[str, Any]:
        payload = await self.get(f"profile/by-discord/{discord_id}")
        if not isinstance(payload, dict) or "uuid" not in payload:
            raise SubtiersAPIError("No Minecraft account is linked to that Discord account.")
        return payload

    async def leaderboard(self, mode: str) -> dict[str, Any]:
        """Retrieve every active player.

        The documented maximum is 50 players *per tier* per request. Pagination
        applies to all five tier buckets at once, so keep asking until every
        bucket returns fewer than 50 entries.
        """
        all_players: dict[str, list[dict[str, Any]]] = {str(number): [] for number in range(1, 6)}
        offset = 0
        while True:
            payload = await self.get(f"mode/{mode}?from={offset}&count=50")
            if not isinstance(payload, dict):
                raise SubtiersAPIError("SubTiers returned an unexpected leaderboard.")
            page_sizes = []
            for tier_number in all_players:
                players = payload.get(tier_number, [])
                if not isinstance(players, list):
                    players = []
                all_players[tier_number].extend(player for player in players if isinstance(player, dict))
                page_sizes.append(len(players))
            if max(page_sizes, default=0) < 50:
                return all_players
            offset += 50

    async def modes(self) -> list[str]:
        payload = await self.get("mode/list")
        if not isinstance(payload, dict):
            return list(MODE_LABELS)
        return list(payload)

    async def overall_players(self) -> list[dict[str, Any]]:
        """Fetch every profile required to calculate an active-only ranking."""
        players: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self.get(f"mode/overall?from={offset}&count=50")
            if not isinstance(payload, list):
                raise SubtiersAPIError("SubTiers returned an unexpected overall leaderboard.")
            players.extend(player for player in payload if isinstance(player, dict))
            if len(payload) < 50:
                return players
            offset += 50


def display_tier(entry: dict[str, Any]) -> str:
    """Format a tier, following the API's instruction to show a retiree's peak."""
    retired = bool(entry.get("retired"))
    tier = entry.get("peak_tier") if retired else entry.get("tier")
    pos = entry.get("peak_pos") if retired else entry.get("pos")
    prefix = "R" if retired else ""
    return f"{prefix}{'HT' if pos == 0 else 'LT'}{tier if tier is not None else '?'}"


def tier_value(entry: dict[str, Any]) -> str:
    tier = display_tier(entry)
    attained = entry.get("attained")
    since = f" since <t:{int(attained)}:D>" if isinstance(attained, (int, float)) else ""
    crown = "👑 " if entry.get("retired") else ""
    base_tier = tier.removeprefix("R")
    # The supplied tier emojis intentionally stop at LT3 / HT3.
    tier_emoji = f"{TIER_EMOJIS[base_tier]} " if base_tier in TIER_EMOJIS else ""
    return f"{crown}{tier_emoji}**{tier}**{since}"


def mode_field_name(mode: str) -> str:
    label = MODE_LABELS.get(mode, mode.replace("_", " ").title())
    return f"{MODE_EMOJIS.get(mode, '🎯')} {label}"


def profile_embed(
    profile: dict[str, Any], *, active_only: bool = False, active_rank: int | None = None, active_points: int | None = None
) -> discord.Embed:
    name = str(profile.get("name", "Unknown player"))
    overall = active_rank if active_only else profile.get("overall", profile.get("all_time_rank", "Unranked"))
    points = active_points if active_only else profile.get("points", 0)
    region = profile.get("region", "Unknown")
    title = f"{name}'s active tiers on SubTiers" if active_only else f"{name}'s tiers on SubTiers"
    embed = discord.Embed(title=title, colour=EMBED_COLOUR)
    embed.set_thumbnail(url=f"{MINEATAR_HEAD}{profile['uuid']}")
    overall_value = f"**#{overall}**" if overall else "Unranked"
    embed.add_field(name="Active Spot" if active_only else "Overall", value=overall_value, inline=True)
    embed.add_field(name="Active Points" if active_only else "Points", value=str(points), inline=True)
    embed.add_field(name="Region", value=str(region), inline=True)
    rankings = profile.get("rankings", {})
    discord_id = profile.get("discord_id")
    embed.add_field(name="Discord", value=f"<@{discord_id}>" if discord_id else "Not linked", inline=True)
    for mode in MODE_LABELS:
        tier = rankings.get(mode)
        if tier and (not active_only or not tier.get("retired")):
            embed.add_field(name=mode_field_name(mode), value=tier_value(tier), inline=True)
    for mode, tier in rankings.items():  # Do not silently drop future API modes.
        if mode not in MODE_LABELS and (not active_only or not tier.get("retired")):
            embed.add_field(name=mode_field_name(mode), value=tier_value(tier), inline=True)
    embed.set_footer(text="Active ranking ignores retired tiers" if active_only else "Data provided by subtiers.net")
    return embed


def cache_paths(mode: str) -> tuple[Path, Path]:
    safe_mode = re.sub(r"[^a-z0-9_-]", "_", mode.lower())
    return CACHE_DIR / f"{safe_mode}-{GRAPH_STYLE_VERSION}.png", CACHE_DIR / f"{safe_mode}-{GRAPH_STYLE_VERSION}.json"


def cache_is_fresh(path: Path) -> bool:
    return path.exists() and (path.stat().st_mtime + CACHE_SECONDS) > time.time()


def active_points(rankings: dict[str, Any]) -> int:
    """Score only a player's current, non-retired ranks; peaks are never used."""
    score = 0
    for tier in rankings.values():
        if not isinstance(tier, dict) or tier.get("retired"):
            continue
        code = f"{'HT' if tier.get('pos') == 0 else 'LT'}{tier.get('tier', '?')}"
        score += TIER_POINTS.get(code, 0)
    return score


def build_active_leaderboard(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    active = []
    for player in players:
        uuid = str(player.get("uuid", ""))
        if not uuid or uuid in seen:
            continue
        seen.add(uuid)
        rankings = player.get("rankings", {})
        points = active_points(rankings if isinstance(rankings, dict) else {})
        if points:
            active.append({"uuid": uuid, "name": str(player.get("name", "Unknown")), "points": points})
    active.sort(key=lambda player: (-player["points"], player["name"].casefold(), player["uuid"]))
    for position, player in enumerate(active, start=1):
        player["rank"] = position
    return active


async def get_active_leaderboard() -> list[dict[str, Any]]:
    if cache_is_fresh(ACTIVE_LEADERBOARD_CACHE):
        try:
            cached = json.loads(ACTIVE_LEADERBOARD_CACHE.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    players = await bot.api.overall_players()
    leaderboard = build_active_leaderboard(players)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_LEADERBOARD_CACHE.write_text(json.dumps(leaderboard), encoding="utf-8")
    return leaderboard


def make_graph(mode: str, leaderboard: dict[str, Any]) -> tuple[Path, int]:
    """Render the API's tier buckets to a PNG and return it with player total."""
    counts: Counter[str] = Counter()
    total = 0
    for tier_number, players in leaderboard.items():
        if not isinstance(players, list):
            continue
        for player in players:
            if not isinstance(player, dict):
                continue
            try:
                tier = int(tier_number)
            except (TypeError, ValueError):
                continue
            if not 1 <= tier <= 5:
                continue
            key = f"{'HT' if player.get('pos') == 0 else 'LT'}{tier}"
            counts[key] += 1
            total += 1

    displayed_tiers = list(TIER_ORDER)
    values = [counts[tier] for tier in displayed_tiers]
    tier_colours = {
        "HT1": "#5d8cf2", "LT1": "#6d9aec", "HT2": "#739ff2", "LT2": "#81b3ed",
        "HT3": "#b4e1f1", "LT3": "#56c994", "HT4": "#57cc91", "LT4": "#ffbd47",
        "HT5": "#ffa940", "LT5": "#ed536c",
    }
    colours = [tier_colours[tier] for tier in displayed_tiers]
    fig, axis = plt.subplots(figsize=(10, 8), dpi=160)
    background = "#0c1422"
    fig.patch.set_facecolor(background)
    axis.set_facecolor(background)
    maximum = max(values, default=0)
    y_limit = max(10, math.ceil(maximum * 1.12 / 500) * 500)
    bars = axis.bar(displayed_tiers, values, color=colours, width=0.76, edgecolor="#d9e4f7", linewidth=0.35, zorder=3)
    fig.suptitle(f"SubTiers - {MODE_LABELS.get(mode, mode.title())}", color="#f8fafc", y=0.96, fontsize=18, weight="bold")
    axis.set_title(f"Region: OVERALL  |  Total Users: {total}", color="#acb9d0", pad=22, fontsize=13)
    axis.set_ylabel("Players", color="#aebbd0", labelpad=12)
    axis.tick_params(colors="#aebbd0", labelsize=11, length=5)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_color("#adb9cb")
    axis.spines["bottom"].set_color("#adb9cb")
    axis.set_ylim(0, y_limit)
    axis.grid(False)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, values):
        percentage = (value / total * 100) if total else 0
        label_y = value + y_limit * 0.045
        axis.text(bar.get_x() + bar.get_width() / 2, label_y, str(value), ha="center", va="bottom", color="#f8fafc", fontsize=11)
        axis.text(bar.get_x() + bar.get_width() / 2, label_y - y_limit * 0.028, f"{percentage:.1f}%", ha="center", va="bottom", color="#aebbd0", fontsize=10)
    fig.tight_layout()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    image_path, metadata_path = cache_paths(mode)
    fig.savefig(image_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    metadata_path.write_text(json.dumps({"mode": mode, "players": total}), encoding="utf-8")
    return image_path, total


class AngelTiers(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.none())
        self.tree = app_commands.CommandTree(self)
        self.api = SubtiersClient()

    async def setup_hook(self) -> None:
        await self.api.start()
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info("Commands synced to development guild %s", dev_guild)
        else:
            await self.tree.sync()
            logging.info("Global commands synced")

    async def close(self) -> None:
        await self.api.close()
        await super().close()


bot = AngelTiers()


@bot.tree.command(name="tier", description="Show a Minecraft player's SubTiers profile.")
@app_commands.describe(player="Minecraft IGN or UUID")
async def tier(interaction: discord.Interaction, player: str) -> None:
    await interaction.response.defer(thinking=True)
    try:
        profile = await bot.api.profile(player)
    except SubtiersAPIError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send(embed=profile_embed(profile))


@bot.tree.command(name="get-user", description="Find the Minecraft account linked to a Discord user.")
@app_commands.describe(user="A Discord ID or @mention")
async def get_user(interaction: discord.Interaction, user: str) -> None:
    match = re.fullmatch(r"<@!?(\d{17,20})>|(\d{17,20})", user.strip())
    if not match:
        await interaction.response.send_message("Enter a Discord ID or a user mention.", ephemeral=True)
        return
    discord_id = int(match.group(1) or match.group(2))
    await interaction.response.defer(thinking=True)
    try:
        profile = await bot.api.profile_by_discord(discord_id)
    except SubtiersAPIError as error:
        await interaction.followup.send(f"<@{discord_id}>: {error}", ephemeral=True)
        return
    embed = profile_embed(profile)
    embed.description = f"Linked Discord account: <@{discord_id}>"
    await interaction.followup.send(embed=embed)


class ActiveLeaderboardView(discord.ui.View):
    """Ten compact buttons that open a selected player's normal tier card."""

    def __init__(self, players: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        for index, player in enumerate(players):
            button = discord.ui.Button(
                label=f"View {player['name']}",
                style=discord.ButtonStyle.secondary,
                row=index // 5,
            )
            button.callback = self.tier_callback(player)
            self.add_item(button)

    @staticmethod
    def tier_callback(player: dict[str, Any]):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                profile = await bot.api.profile(str(player["uuid"]))
            except SubtiersAPIError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            await interaction.followup.send(embed=profile_embed(profile), ephemeral=True)

        return callback


@bot.tree.command(name="activelb", description="Show the active-only SubTiers leaderboard or a player's active tiers.")
@app_commands.describe(username="Optional Minecraft username or UUID")
async def active_lb(interaction: discord.Interaction, username: str | None = None) -> None:
    await interaction.response.defer(thinking=True)
    try:
        if username:
            profile = await bot.api.profile(username)
            leaderboard = await get_active_leaderboard()
            player = next((item for item in leaderboard if item["uuid"] == profile["uuid"]), None)
            points = player["points"] if player else active_points(profile.get("rankings", {}))
            rank = player["rank"] if player else None
            await interaction.followup.send(embed=profile_embed(profile, active_only=True, active_rank=rank, active_points=points))
            return

        leaderboard = await get_active_leaderboard()
    except SubtiersAPIError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except (OSError, ValueError, json.JSONDecodeError):
        logging.exception("Could not build active leaderboard")
        await interaction.followup.send("Could not build the active leaderboard right now.", ephemeral=True)
        return

    top_ten = leaderboard[:10]
    if not top_ten:
        await interaction.followup.send("There are no players with active tiers right now.", ephemeral=True)
        return
    rows = [f"**#{player['rank']}** {player['name']} — **{player['points']}** active points" for player in top_ten]
    embed = discord.Embed(title="SubTiers Active Leaderboard", description="\n".join(rows), colour=EMBED_COLOUR)
    embed.set_footer(text="Current tiers only — retired tiers and peak tiers are ignored")
    await interaction.followup.send(embed=embed, view=ActiveLeaderboardView(top_ten))


@bot.tree.command(name="graph", description="Create or retrieve a tier-distribution graph for a SubTiers gamemode.")
@app_commands.describe(gamemode="A SubTiers gamemode", update="Fetch a new graph instead of using the one-hour cache")
async def graph(interaction: discord.Interaction, gamemode: str, update: bool = False) -> None:
    mode = gamemode.strip().lower().replace(" ", "_")
    await interaction.response.defer(thinking=True)
    try:
        modes = await bot.api.modes()
        if mode not in modes:
            choices = ", ".join(MODE_LABELS.get(item, item) for item in modes)
            await interaction.followup.send(f"Unknown gamemode. Available modes: {choices}", ephemeral=True)
            return
        image_path, metadata_path = cache_paths(mode)
        cached = not update and cache_is_fresh(image_path)
        if cached:
            player_total = json.loads(metadata_path.read_text(encoding="utf-8")).get("players", "?") if metadata_path.exists() else "?"
        else:
            leaderboard = await bot.api.leaderboard(mode)
            image_path, player_total = await asyncio.to_thread(make_graph, mode, leaderboard)
    except (SubtiersAPIError, OSError, ValueError, json.JSONDecodeError) as error:
        logging.exception("Could not make graph")
        await interaction.followup.send(f"Could not create that graph: {error}", ephemeral=True)
        return
    label = MODE_LABELS.get(mode, mode.replace("_", " ").title())
    cache_note = "cached" if cached else "updated"
    await interaction.followup.send(
        content=f"**{label}** tier distribution — {player_total} ranked players ({cache_note}).",
        file=discord.File(image_path, filename=f"{mode}-tiers.png"),
    )


@graph.autocomplete("gamemode")
async def gamemode_autocomplete(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    query = current.lower().replace(" ", "_")
    return [
        app_commands.Choice(name=label, value=key)
        for key, label in MODE_LABELS.items()
        if query in key or query in label.lower()
    ][:25]


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token or token.startswith("YOUR_NEW_BOT_TOKEN"):
        raise SystemExit("Set DISCORD_TOKEN in .env before starting the bot.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
