import argparse
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set
from urllib.parse import urljoin

import discord
import requests
from bs4 import BeautifulSoup
from discord.ext import commands

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USERS_FILE = Path("users.json")
CHECK_INTERVAL_SECONDS = 60

SITES_CONFIG: Dict[str, Dict[str, Any]] = {
    "ippodo_global": {
        "url": "https://global.ippodo-tea.co.jp/collections/matcha",
        "product_card_selector": "li.m-product-card",
        "out_of_stock_filter": "button.out-of-stock",
        "name_selector": ".m-product-card__name a",
        "href_selector": ".m-product-card__name a",
        "base_url": "https://global.ippodo-tea.co.jp",
        "current_products": set(),
    },
    "marukyu_koyamaen": {
        "url": "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha",
        "product_card_selector": "li.instock",
        "out_of_stock_filter": None,
        "name_selector": ".product-name h4",
        "href_selector": "a.woocommerce-loop-product__link",
        "base_url": "https://www.marukyu-koyamaen.co.jp",
        "current_products": set(),
    },
}


def load_users() -> Set[int]:
    """Loads user IDs from USERS_FILE."""
    try:
        return set(json.loads(USERS_FILE.read_text())) if USERS_FILE.exists() else set()
    except Exception as e:
        logger.error(f"Error reading users file {USERS_FILE}: {e}")
        return set()


def save_users(user_id_set: Set[int]):
    """Saves user IDs to USERS_FILE."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        USERS_FILE.write_text(json.dumps(list(user_id_set)))
    except Exception as e:
        logger.error(f"Failed to save user IDs to {USERS_FILE}: {e}", exc_info=True)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
subscribed_user_ids: Set[int] = load_users()


def format_product_diff_message(added: Set[str], removed: Set[str]) -> str:
    """Formats a message showing product stock changes."""
    parts: list[str] = []
    if added:
        parts.append(f"🟢 Now in stock: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"🔴 Out of stock: {', '.join(sorted(removed))}")
    return "\n".join(parts)


def fetch_products_from_site(
    site_key: str, site_config: Dict[str, Any], session: requests.Session
) -> Optional[Set[str]]:
    """Fetches product names and links from a site using requests and BeautifulSoup.

    Returns a set of product strings or None if an error occurred.
    """
    products: Set[str] = set()
    try:
        response = session.get(site_config["url"], timeout=10.0)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        product_cards = soup.select(site_config["product_card_selector"])
        if not product_cards:
            logger.warning(
                f"No product cards found for {site_key} using selector '{site_config['product_card_selector']}'."
            )
            return set()

        for card in product_cards:
            if oos_filter := site_config.get("out_of_stock_filter"):
                if card.select_one(oos_filter):
                    continue

            href = ""
            href_elem = card.select_one(site_config["href_selector"])
            if href_elem and (isinstance(href_value := href_elem.get("href"), str)):
                href = urljoin(site_config["base_url"], href_value)

            name_elem = card.select_one(site_config["name_selector"])
            if name_elem:
                name = name_elem.get_text(strip=True)
                if name:
                    products.add(f"[{name}]({href})" if href else name)
                else:
                    logger.warning(
                        f"Found name element but no text content for a product on {site_key}"
                    )
            else:
                logger.warning(
                    f"Name selector '{site_config['name_selector']}' not found for a card on {site_key}"
                )
        return products

    except requests.exceptions.HTTPError as e:
        logger.error(
            f"HTTP error {e.response.status_code} fetching products from {site_key}: {e.request.url if e.request else site_config['url']}",
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Request error fetching products from {site_key}: {e}",
        )
    except Exception as e:
        logger.error(
            f"Error parsing products from {site_key}: {e}",
            exc_info=True,
        )
    return None


async def check_all_sites_task():
    """Periodically checks all configured sites for product stock changes."""
    with requests.Session() as session:
        session.headers.update(
            {"User-Agent": "DiscordProductNotifierBot/1.0 (Python/requests)"}
        )

        try:
            while True:
                for site_key, config in SITES_CONFIG.items():
                    logger.info(f"Checking site: {site_key}")

                    fetched_products = await bot.loop.run_in_executor(
                        None, fetch_products_from_site, site_key, config, session
                    )

                    if fetched_products is None:
                        logger.warning(
                            f"Skipping update for {site_key} due to fetch/parse error."
                        )
                        continue

                    if fetched_products == config["current_products"]:
                        logger.info(f"No changes for {site_key}.")
                        continue

                    added = fetched_products - config["current_products"]
                    removed = config["current_products"] - fetched_products
                    config["current_products"] = fetched_products

                    logger.info(
                        f"Changes detected for {site_key}. Added: {len(added)}, Removed: {len(removed)}"
                    )
                    message = format_product_diff_message(added, removed)
                    for user_id in subscribed_user_ids:
                        try:
                            user = await bot.fetch_user(user_id)
                            await user.send(message)
                        except discord.NotFound:
                            logger.warning(
                                f"User with ID {user_id} not found. Removing from subscription."
                            )
                            subscribed_user_ids.discard(user_id)
                        except Exception as e:
                            logger.error(
                                f"Error sending DM to {user_id}: {e}", exc_info=True
                            )

                await discord.utils.sleep_until(
                    discord.utils.utcnow()
                    + datetime.timedelta(seconds=CHECK_INTERVAL_SECONDS)
                )
        except Exception as e:
            logger.error(f"Critical error in site check task: {e}", exc_info=True)


@bot.tree.command(name="subscribe", description="Subscribe to product notifications.")
async def subscribe(interaction: discord.Interaction):
    """Adds the user to the subscription list."""
    if interaction.user.id not in subscribed_user_ids:
        subscribed_user_ids.add(interaction.user.id)
        save_users(subscribed_user_ids)
        await interaction.response.send_message(
            "You are now subscribed.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "You are already subscribed.", ephemeral=True
        )


@bot.tree.command(
    name="unsubscribe", description="Unsubscribe from product notifications."
)
async def unsubscribe(interaction: discord.Interaction):
    """Removes the user from the subscription list."""
    if interaction.user.id in subscribed_user_ids:
        subscribed_user_ids.discard(interaction.user.id)
        save_users(subscribed_user_ids)
        await interaction.response.send_message(
            "You have been unsubscribed.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "You are not currently subscribed.", ephemeral=True
        )


@bot.event
async def on_ready():
    """Called when the bot is ready and connected."""
    try:
        logger.info(f"{bot.user} has connected to Discord!")
        await bot.tree.sync()
        logger.info("Commands synced.")
        bot.loop.create_task(check_all_sites_task())
        logger.info("Site checking task started.")
    except Exception as e:
        logger.error(f"Error during on_ready: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discord bot for product stock monitoring."
    )
    parser.add_argument("--token", type=str, required=True, help="Discord bot token.")
    args = parser.parse_args()

    try:
        bot.run(args.token)
    except Exception as e:
        logger.critical(
            f"An unexpected error occurred while running the bot: {e}", exc_info=True
        )
