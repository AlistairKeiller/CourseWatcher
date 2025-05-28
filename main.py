import argparse
import datetime
import json
import logging
from pathlib import Path
from typing import Set, Dict, Any, Optional
from urllib.parse import urljoin

import discord
from discord.ext import commands
from playwright.async_api import (
    Playwright,
    async_playwright,
    Browser,
    Page,
)

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
        "site_name_md": "[Ippodo Global](https://global.ippodo-tea.co.jp/collections/matcha)",
        "current_products": set(),
    },
    "ippodo_us": {
        "url": "https://ippodotea.com/collections/matcha",
        "product_card_selector": "div.matcha-card",
        "out_of_stock_filter": "button.btn-unavailable",
        "name_selector": ".product-title a",
        "href_selector": ".product-title a",
        "base_url": "https://ippodotea.com",
        "site_name_md": "[Ippodo US](https://ippodotea.com/collections/matcha)",
        "current_products": set(),
    },
    "marukyu_koyamaen": {
        "url": "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha",
        "product_card_selector": "li.instock",
        "out_of_stock_filter": None,
        "name_selector": ".product-name h4",
        "href_selector": "a.woocommerce-loop-product__link",
        "base_url": "https://www.marukyu-koyamaen.co.jp",
        "site_name_md": "[Marukyu Koyamaen](https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha)",
        "current_products": set(),
    },
}


def load_users() -> Set[int]:
    """Loads user IDs from USERS_FILE."""
    return set(json.loads(USERS_FILE.read_text())) if USERS_FILE.exists() else set()


def save_users(user_id_set: Set[int]):
    """Saves user IDs to USERS_FILE."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(list(user_id_set)))


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
subscribed_user_ids: Set[int] = load_users()
browser: Optional[Browser] = None
playwright: Optional[Playwright] = None


def format_product_diff_message(
    site_name_md: str, added: Set[str], removed: Set[str]
) -> str:
    """Formats a message showing product stock changes."""
    parts: list[str] = []
    if added:
        parts.append(f"🟢 Now in stock: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"🔴 Out of stock: {', '.join(sorted(removed))}")
    return f"{site_name_md}\n" + "\n".join(parts)


async def fetch_products_from_site(
    site_config: Dict[str, Any], page: Page
) -> Optional[Set[str]]:
    """Fetches product names and links from a site using a Playwright page.

    Returns a set of product strings or None if an error occurred.
    """
    products: Set[str] = set()
    try:
        await page.goto(site_config["url"], timeout=60000)
        await page.wait_for_selector(
            site_config["product_card_selector"], timeout=30000
        )

        product_cards = await page.query_selector_all(
            site_config["product_card_selector"]
        )
        if not product_cards:
            return None

        for card in product_cards:
            if oos_filter := site_config.get("out_of_stock_filter"):
                if await card.query_selector(oos_filter):
                    continue
            href = ""
            if href_elem := await card.query_selector(site_config["href_selector"]):
                if href_attr := await href_elem.get_attribute("href"):
                    href = urljoin(site_config["base_url"], href_attr.strip())
            if name_elem := await card.query_selector(site_config["name_selector"]):
                if name := await name_elem.text_content():
                    products.add(f"[{name.strip()}]({href})" if href else name.strip())
                else:
                    logger.warning(
                        f"Found name element but no text content for a product on {site_config['site_name_md']}"
                    )
            else:
                logger.warning(
                    f"Name selector {site_config['name_selector']} not found for a card on {site_config['site_name_md']}"
                )
        return products
    except Exception as e:
        logger.error(
            f"Error fetching products from {site_config['site_name_md']}: {e}",
            exc_info=True,
        )
        return None


async def check_all_sites_task():
    """Periodically checks all configured sites for product stock changes."""
    if not browser:
        logger.error("Browser not initialized. Aborting site check task.")
        return

    page = await browser.new_page()
    try:
        while True:
            for site_key, config in SITES_CONFIG.items():
                fetched_products = await fetch_products_from_site(config, page)

                if fetched_products is None:
                    logger.warning(
                        f"Skipping update for {config['site_name_md']} due to fetch error."
                    )
                    continue

                if fetched_products == config["current_products"]:
                    continue
                added = fetched_products - config["current_products"]
                removed = config["current_products"] - fetched_products
                config["current_products"] = fetched_products
                message = format_product_diff_message(
                    config["site_name_md"], added, removed
                )
                for user_id in subscribed_user_ids:
                    try:
                        user = await bot.fetch_user(user_id)
                        await user.send(message)
                    except Exception as e:
                        logger.error(
                            f"Error sending DM to {user_id}: {e}", exc_info=True
                        )
            await discord.utils.sleep_until(
                discord.utils.utcnow()
                + datetime.timedelta(seconds=CHECK_INTERVAL_SECONDS)
            )
    except Exception as e:
        logger.error(f"Error in site check task: {e}", exc_info=True)
    finally:
        await page.close()


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
    global browser, playwright
    try:
        await bot.tree.sync()
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        bot.loop.create_task(check_all_sites_task())
    except Exception as e:
        logger.error(f"Error during on_ready: {e}", exc_info=True)


@bot.event
async def on_disconnect():
    """Called when the bot disconnects."""
    if browser:
        await browser.close()
    if playwright:
        await playwright.stop()


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
