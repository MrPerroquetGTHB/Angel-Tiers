from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


API_BASE = "https://subtiers.net/api/v2"
PUBLIC_API_BASE = "https://subtiers.net/api"
MINEATAR_HEAD = "https://api.mineatar.io/head/"
EMBED_COLOUR = discord.Colour.from_rgb(135, 206, 250)
CACHE_SECONDS = 60 * 60
CACHE_DIR = Path("data/cache")
GRAPH_STYLE_VERSION = "v4"
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)

MODE_LABELS = {
    "minecart": "Minecart",
    "dia_crystal": "Diamond Vanilla",
    "dia_smp": "Diamond SMP",
    "bed": "Bed",
    "bow": "Bow",
    "speed": "Speed",
    "creeper": "Creeper",
    "og_vanilla": "OG Vanilla",
    "debuff": "DeBuff",
    "elytra": "Elytra",
    "manhunt": "Manhunt",
    "trident": "Trident",
}
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
    "HT3": "<:ht3:1550998544238772316>",
}
TIER_ORDER = (
    "HT1", "LT1", "HT2", "LT2", "HT3",
    "LT3", "HT4", "LT4", "HT5", "LT5",
)
TIER_POINTS = {
    "HT1": 60,
    "LT1": 45,
    "HT2": 30,
    "LT2": 20,
    "HT3": 10,
    "LT3": 6,
    "HT4": 4,
    "LT4": 3,
    "HT5": 2,
    "LT5": 1,
}


class SubtiersAPIError(Exception):
    pass


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
        return await self.get_url(f"{API_BASE}/{path.lstrip('/')}")

    async def get_public(self, path: str) -> Any:
        return await self.get_url(f"{PUBLIC_API_BASE}/{path.lstrip('/')}")

    async def get_url(self, url: str) -> Any:
        if self.session is None:
            raise RuntimeError("HTTP session is not ready")
        try:
            for attempt in range(4):
                async with self.session.get(url) as response:
                    if response.status == 404:
                        raise SubtiersAPIError("No linked SubTiers account was found.")
                    if response.status < 400:
                        return await response.json()
                    if response.status != 429:
                        raise SubtiersAPIError("SubTiers could not process that request right now.")
                    retry_after = float(response.headers.get("Retry-After", "15"))
                if attempt == 3:
                    raise SubtiersAPIError("SubTiers is rate-limiting requests. Please try again shortly.")
                await asyncio.sleep(min(max(retry_after, 1), 70))
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

    async def leaderboard(self, mode: str) -> dict[str, list[dict[str, Any]]]:
        players_by_tier = {str(tier): [] for tier in range(1, 6)}
        offset = 0
        while True:
            payload = await self.get(f"mode/{mode}?from={offset}&count=50")
            if not isinstance(payload, dict):
                raise SubtiersAPIError("SubTiers returned an unexpected leaderboard.")
            page_sizes: list[int] = []
            for tier, players in players_by_tier.items():
                page = payload.get(tier, [])
                page = page if isinstance(page, list) else []
                players.extend(player for player in page if isinstance(player, dict))
                page_sizes.append(len(page))
            # All tier buckets share the same offset, so a full bucket means another page exists.
            if max(page_sizes) < 50:
                return players_by_tier
            offset += 50

    async def modes(self) -> list[str]:
        payload = await self.get("mode/list")
        if not isinstance(payload, dict):
            return list(MODE_LABELS)
        return list(payload)

    async def active_leaderboard(self) -> list[dict[str, Any]]:
        payload = await self.get_public("leaderboard/active")
        players = payload.get("players") if isinstance(payload, dict) else None
        if not isinstance(players, list):
            raise SubtiersAPIError("SubTiers returned an unexpected active leaderboard.")
        return [player for player in players if isinstance(player, dict)]

    async def top_players(self) -> list[dict[str, Any]]:
        players: dict[str, dict[str, Any]] = {}
        offset = 0
        while offset < 100:
            payload = await self.get_public(f"leaderboard?mode=overall&offset={offset}&count=50")
            page = payload.get("players") if isinstance(payload, dict) else None
            if not isinstance(page, list) or not page:
                break
            for player in page:
                if not isinstance(player, dict) or not player.get("uuid"):
                    continue
                rank = player.get("rank")
                if isinstance(rank, int) and 1 <= rank <= 100:
                    players[str(player["uuid"])] = player
            if not payload.get("hasMore"):
                break
            offset += 50
        return list(players.values())

    async def active_player(self, identifier: str) -> dict[str, Any]:
        payload = await self.get_public(f"points/active/{quote(identifier.strip(), safe='')}")
        if not isinstance(payload, dict) or "uuid" not in payload:
            raise SubtiersAPIError("SubTiers returned unexpected active player data.")
        return payload


def display_tier(entry: dict[str, Any]) -> str:
    retired = bool(entry.get("retired"))
    tier = entry.get("peak_tier") if retired else entry.get("tier")
    pos = entry.get("peak_pos") if retired else entry.get("pos")
    prefix = "R" if retired else ""
    return f"{prefix}{'HT' if pos == 0 else 'LT'}{tier if tier is not None else '?'}"


def tier_value(entry: dict[str, Any]) -> str:
    tier = display_tier(entry)
    crown = "👑 " if entry.get("retired") else ""
    current_tier = tier.removeprefix("R")
    emoji = f"{TIER_EMOJIS.get(current_tier, '')} " if current_tier in TIER_EMOJIS else ""
    points = TIER_POINTS.get(current_tier, 0)
    attained = entry.get("attained")
    since = f" since <t:{int(attained)}:D>" if isinstance(attained, (int, float)) else ""
    peak = ""
    if not entry.get("retired") and entry.get("peak_tier") is not None and entry.get("peak_pos") is not None:
        peak_tier = f"{'HT' if entry['peak_pos'] == 0 else 'LT'}{entry['peak_tier']}"
        if TIER_POINTS.get(peak_tier, 0) > points:
            peak = f" (p{peak_tier})"
    return f"{crown}{emoji}**{tier}** · **{points} pts**{peak}{since}"


def tier_score(entry: dict[str, Any] | None) -> int:
    if not entry:
        return 0
    return TIER_POINTS.get(display_tier(entry).removeprefix("R"), 0)


def mode_field_name(mode: str) -> str:
    label = MODE_LABELS.get(mode, mode.replace("_", " ").title())
    return f"{MODE_EMOJIS.get(mode, '🎯')} {label}"


def get_rankings(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rankings = profile.get("rankings")
    return rankings if isinstance(rankings, dict) else {}


def ordered_modes(*rankings: dict[str, dict[str, Any]]) -> list[str]:
    modes = list(MODE_LABELS)
    for ranking in rankings:
        modes.extend(mode for mode in ranking if mode not in modes)
    return modes


def overall_rank(profile: dict[str, Any]) -> Any:
    return profile.get("overall") or profile.get("all_time_rank")


def format_rank(rank: Any) -> str:
    return f"**#{rank}**" if rank else "Unranked"


def profile_embed(
    profile: dict[str, Any], *, active_only: bool = False, active_rank: int | None = None, active_points: int | None = None
) -> discord.Embed:
    name = str(profile.get("name", "Unknown player"))
    rank = active_rank if active_only else overall_rank(profile)
    points = active_points if active_only else profile.get("points", 0)
    title = f"{name}'s active tiers on SubTiers" if active_only else f"{name}'s tiers on SubTiers"
    embed = discord.Embed(title=title, colour=EMBED_COLOUR)
    embed.set_thumbnail(url=f"{MINEATAR_HEAD}{profile['uuid']}")
    embed.add_field(name="Active Spot" if active_only else "Overall", value=format_rank(rank), inline=True)
    embed.add_field(name="Active Points" if active_only else "Points", value=str(points), inline=True)
    embed.add_field(name="Region", value=str(profile.get("region", "Unknown")), inline=True)
    discord_id = profile.get("discord_id")
    embed.add_field(name="Discord", value=f"<@{discord_id}>" if discord_id else "Not linked", inline=True)
    rankings = get_rankings(profile)
    for mode in ordered_modes(rankings):
        tier = rankings.get(mode)
        if tier and (not active_only or not tier.get("retired")):
            embed.add_field(name=mode_field_name(mode), value=tier_value(tier), inline=True)
    embed.set_footer(text="Current tiers only — retired tiers are excluded" if active_only else "subtiers.net")
    return embed


def comparison_embed(first: dict[str, Any], second: dict[str, Any]) -> discord.Embed:
    first_name = str(first.get("name", "Unknown player"))
    second_name = str(second.get("name", "Unknown player"))
    embed = discord.Embed(
        title=f"{first_name} vs {second_name}",
        description=(
            f"**{first_name}** — {format_rank(overall_rank(first))} · "
            f"**{first.get('points', 0)} pts** · {first.get('region', 'Unknown')}\n"
            f"**{second_name}** — {format_rank(overall_rank(second))} · "
            f"**{second.get('points', 0)} pts** · {second.get('region', 'Unknown')}\n\n"
            f"Tiers are shown as **{first_name} / {second_name}**. "
        ),
        colour=EMBED_COLOUR,
    )
    first_rankings = get_rankings(first)
    second_rankings = get_rankings(second)
    rows = []
    for mode in ordered_modes(first_rankings, second_rankings):
        first_tier = first_rankings.get(mode)
        second_tier = second_rankings.get(mode)
        if not first_tier and not second_tier:
            continue
        first_value = display_tier(first_tier) if first_tier else "Unranked"
        second_value = display_tier(second_tier) if second_tier else "Unranked"
        first_score = tier_score(first_tier)
        second_score = tier_score(second_tier)
        if first_score > second_score:
            first_value = f"**{first_value}**"
        elif second_score > first_score:
            second_value = f"**{second_value}**"
        rows.append(f"{mode_field_name(mode)} — {first_value} / {second_value}")

    if rows:
        split_at = math.ceil(len(rows) / 2)
        for index, start in enumerate(range(0, len(rows), split_at)):
            title = "Gamemodes" if index == 0 else "Gamemodes (cont.)"
            embed.add_field(name=title, value="\n".join(rows[start:start + split_at]), inline=True)
    embed.set_footer(text="subtiers.net")
    return embed


def cache_paths(mode: str) -> tuple[Path, Path]:
    safe_mode = re.sub(r"[^a-z0-9_-]", "_", mode.lower())
    image_path = CACHE_DIR / f"{safe_mode}-{GRAPH_STYLE_VERSION}.png"
    metadata_path = CACHE_DIR / f"{safe_mode}-{GRAPH_STYLE_VERSION}.json"
    return image_path, metadata_path


def cache_is_fresh(path: Path) -> bool:
    return path.exists() and (path.stat().st_mtime + CACHE_SECONDS) > time.time()


def cached_player_total(metadata_path: Path) -> int | str:
    if not metadata_path.exists():
        return "?"
    return json.loads(metadata_path.read_text(encoding="utf-8")).get("players", "?")


def make_graph(mode: str, leaderboard: dict[str, list[dict[str, Any]]]) -> tuple[Path, int]:
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

    tiers = list(TIER_ORDER)
    values = [counts[tier] for tier in tiers]
    tier_colours = {
        "HT1": "#5d8cf2",
        "LT1": "#6d9aec",
        "HT2": "#739ff2",
        "LT2": "#81b3ed",
        "HT3": "#b4e1f1",
        "LT3": "#56c994",
        "HT4": "#57cc91",
        "LT4": "#ffbd47",
        "HT5": "#ffa940",
        "LT5": "#ed536c",
    }
    colours = [tier_colours[tier] for tier in tiers]
    fig, axis = plt.subplots(figsize=(10, 8), dpi=160)
    background = "#0c1422"
    fig.patch.set_facecolor(background)
    axis.set_facecolor(background)
    maximum = max(values, default=0)
    y_limit = max(10, math.ceil(maximum * 1.12 / 500) * 500)
    bars = axis.bar(tiers, values, color=colours, width=0.76, edgecolor="#d9e4f7", linewidth=0.35, zorder=3)
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
        x_position = bar.get_x() + bar.get_width() / 2
        count_position = value + y_limit * 0.045
        axis.text(x_position, count_position, str(value), ha="center", va="bottom", color="#f8fafc", fontsize=11)
        axis.text(x_position, count_position - y_limit * 0.028, f"{percentage:.1f}%", ha="center", va="bottom", color="#aebbd0", fontsize=10)
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
        self.tree = app_commands.CommandTree(
            self,
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        )
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


@bot.tree.command(name="compare", description="Compare two Minecraft players' SubTiers profiles.")
@app_commands.describe(player1="First Minecraft IGN or UUID", player2="Second Minecraft IGN or UUID")
async def compare(interaction: discord.Interaction, player1: str, player2: str) -> None:
    await interaction.response.defer(thinking=True)
    try:
        first, second = await asyncio.gather(bot.api.profile(player1), bot.api.profile(player2))
    except SubtiersAPIError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send(embed=comparison_embed(first, second))


@bot.tree.command(name="random", description="Show a random player from the top 100 overall.")
async def random_player(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        players = await bot.api.top_players()
        if not players:
            await interaction.followup.send(
                "No ranked players were found in the top 100.",
                ephemeral=True,
            )
            return
        selected = random.choice(players)
        profile = await bot.api.profile(str(selected["uuid"]))
    except SubtiersAPIError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    embed = profile_embed(profile)
    embed.title = "Random player from the top 100"
    embed.description = f"Selected: **{profile.get('name', selected.get('name', 'Unknown player'))}**"
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="pointvalue", description="Show the point value of every SubTiers tier.")
async def point_value(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="Point Values", colour=EMBED_COLOUR)
    for tier in TIER_ORDER:
        emoji = f"{TIER_EMOJIS[tier]} " if tier in TIER_EMOJIS else ""
        embed.add_field(name=f"{emoji}{tier}", value=f"**{TIER_POINTS[tier]}** points", inline=True)
    embed.set_footer(text="subtiers.net")
    await interaction.response.send_message(embed=embed)


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
    PAGE_SIZE = 10

    def __init__(self, players: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.players = players
        self.page = 0
        self.update_buttons()

    @property
    def page_count(self) -> int:
        return math.ceil(len(self.players) / self.PAGE_SIZE)

    def current_players(self) -> list[dict[str, Any]]:
        start = self.page * self.PAGE_SIZE
        return self.players[start:start + self.PAGE_SIZE]

    def build_embed(self) -> discord.Embed:
        rows = [
            f"**#{player.get('rank', '?')}** {player.get('name', 'Unknown')} - "
            f"**{player.get('points', 0)}** active points"
            for player in self.current_players()
        ]
        embed = discord.Embed(
            title="SubTiers Active Leaderboard",
            description="\n".join(rows),
            colour=EMBED_COLOUR,
        )
        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{self.page_count} • active tiers only - "
                "retired tiers and peaks don't count"
            )
        )
        return embed

    def update_buttons(self) -> None:
        self.clear_items()
        for index, player in enumerate(self.current_players()):
            button = discord.ui.Button(
                label=f"View {player.get('name', 'Unknown')}"[:80],
                style=discord.ButtonStyle.secondary,
                row=index // 5,
            )
            button.callback = self.tier_callback(str(player.get("uuid", "")))
            self.add_item(button)

        previous = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.primary,
            row=2,
            disabled=self.page == 0,
        )
        previous.callback = self.go_to_previous_page
        self.add_item(previous)

        next_page = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.primary,
            row=2,
            disabled=self.page >= self.page_count - 1,
        )
        next_page.callback = self.go_to_next_page
        self.add_item(next_page)

    async def go_to_previous_page(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def go_to_next_page(self, interaction: discord.Interaction) -> None:
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @staticmethod
    def tier_callback(uuid: str):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                profile = await bot.api.profile(uuid)
            except SubtiersAPIError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            await interaction.followup.send(embed=profile_embed(profile), ephemeral=True)

        return callback


@bot.tree.command(name="activelb", description="Show the active SubTiers leaderboard or a player's active tiers.")
@app_commands.describe(username="Optional Minecraft username")
async def active_lb(interaction: discord.Interaction, username: str | None = None) -> None:
    await interaction.response.defer(thinking=True)
    try:
        if username:
            active = await bot.api.active_player(username)
            profile = await bot.api.profile(str(active["uuid"]))
            await interaction.followup.send(
                embed=profile_embed(
                    profile,
                    active_only=True,
                    active_rank=active.get("rank"),
                    active_points=active.get("points", 0),
                )
            )
            return

        players = await bot.api.active_leaderboard()
    except SubtiersAPIError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    if not players:
        await interaction.followup.send("There are no active players right now.", ephemeral=True)
        return
    view = ActiveLeaderboardView(players)
    await interaction.followup.send(embed=view.build_embed(), view=view)


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
            player_total = cached_player_total(metadata_path)
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
