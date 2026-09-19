"""Angel Tiers: a Discord bot for the public SubTiers v2 API."""

from __future__ import annotations

import asyncio
import json
import logging
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
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")

# This is also the ordering used by SubTiers' own UI.
MODE_LABELS = {
    "minecart": "Minecart", "dia_crystal": "Diamond Vanilla", "dia_smp": "Diamond SMP",
    "bed": "Bed", "bow": "Bow", "speed": "Speed", "creeper": "Creeper",
    "og_vanilla": "OG Vanilla", "debuff": "DeBuff", "elytra": "Elytra",
    "manhunt": "Manhunt", "trident": "Trident",
}
TIER_ORDER = ("HT1", "LT1", "HT2", "LT2", "HT3", "LT3", "HT4", "LT4", "HT5", "LT5")


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


def display_tier(entry: dict[str, Any]) -> str:
    """Format a tier, following the API's instruction to show a retiree's peak."""
    retired = bool(entry.get("retired"))
    tier = entry.get("peak_tier") if retired else entry.get("tier")
    pos = entry.get("peak_pos") if retired else entry.get("pos")
    return f"{'R' if retired else ''}{'HT' if pos == 0 else 'LT'}{tier if tier is not None else '?'}"


def tier_value(entry: dict[str, Any]) -> str:
    attained = entry.get("attained")
    since = f" since <t:{int(attained)}:D>" if isinstance(attained, (int, float)) else ""
    return f"**{display_tier(entry)}**{since}"


def profile_embed(profile: dict[str, Any]) -> discord.Embed:
    name = str(profile.get("name", "Unknown player"))
    overall = profile.get("overall", profile.get("all_time_rank", "Unranked"))
    points = profile.get("points", 0)
    region = profile.get("region", "Unknown")
    embed = discord.Embed(title=f"{name}'s tiers on SubTiers", colour=EMBED_COLOUR)
    embed.set_thumbnail(url=f"{MINEATAR_HEAD}{profile['uuid']}")
    embed.add_field(name="Overall", value=f"**#{overall}**", inline=True)
    embed.add_field(name="Points", value=str(points), inline=True)
    embed.add_field(name="Region", value=str(region), inline=True)
    rankings = profile.get("rankings", {})
    discord_id = profile.get("discord_id")
    embed.add_field(name="Discord", value=f"<@{discord_id}>" if discord_id else "Not linked", inline=True)
    for mode in MODE_LABELS:
        tier = rankings.get(mode)
        if tier:
            embed.add_field(name=MODE_LABELS[mode], value=tier_value(tier), inline=True)
    for mode, tier in rankings.items():  # Do not silently drop future API modes.
        if mode not in MODE_LABELS:
            embed.add_field(name=mode.replace("_", " ").title(), value=tier_value(tier), inline=True)
    embed.set_footer(text="Data provided by subtiers.net")
    return embed


def cache_paths(mode: str) -> tuple[Path, Path]:
    safe_mode = re.sub(r"[^a-z0-9_-]", "_", mode.lower())
    return CACHE_DIR / f"{safe_mode}.png", CACHE_DIR / f"{safe_mode}.json"


def cache_is_fresh(path: Path) -> bool:
    return path.exists() and (path.stat().st_mtime + CACHE_SECONDS) > time.time()


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

    displayed_tiers = [tier for tier in TIER_ORDER if counts[tier] > 0]
    values = [counts[tier] for tier in displayed_tiers]
    # Match the colourful ascending distribution style in the supplied mockup.
    colours = ["#5ecdf0", "#8764e8", "#3a84e8", "#49bbea", "#63dfe7", "#3dce9a", "#40ca8d", "#ffc329", "#ff933d", "#e85d77"]
    fig, axis = plt.subplots(figsize=(10, 8), dpi=160)
    fig.patch.set_facecolor("#111827")
    axis.set_facecolor("#111827")
    bars = axis.bar(displayed_tiers, values, color=colours[:len(values)], width=0.8)
    fig.suptitle(f"SubTiers - {MODE_LABELS.get(mode, mode.title())}", color="white", y=0.96, fontsize=16)
    axis.set_title(f"Region: OVERALL | Total Users: {total}", color="#f3f4f6", pad=20, fontsize=13)
    axis.set_ylabel("Players", color="#d6e6f5")
    axis.tick_params(colors="#d6e6f5")
    for spine in axis.spines.values():
        spine.set_color("#e5e7eb")
    axis.grid(False)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, values):
        percentage = (value / total * 100) if total else 0
        axis.text(bar.get_x() + bar.get_width() / 2, value, f"{value}\n{percentage:.1f}%", ha="center", va="bottom", color="white", fontsize=9)
    axis.text(0.99, 0.012, "Made by Angel Tiers", transform=axis.transAxes, ha="right", va="bottom", color="#ffae2b", fontsize=10)
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
@app_commands.describe(user="Mention or select the Discord user")
async def get_user(interaction: discord.Interaction, user: discord.User) -> None:
    await interaction.response.defer(thinking=True)
    try:
        profile = await bot.api.profile_by_discord(user.id)
    except SubtiersAPIError as error:
        await interaction.followup.send(f"{user.mention}: {error}", ephemeral=True)
        return
    embed = profile_embed(profile)
    embed.description = f"Linked Discord account: {user.mention}"
    await interaction.followup.send(embed=embed)


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
