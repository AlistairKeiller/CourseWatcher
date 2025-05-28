import argparse
import datetime
import json
import logging
from typing import Set, Dict, Any, Optional

import discord
from discord.ext import commands
from playwright.async_api import (
    Playwright,
    async_playwright,
    Browser,
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USERS_FILE = "users.json"
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
    """Loads user IDs from the JSON file."""
    try:
        with open(USERS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        logger.info(f"{USERS_FILE} not found or invalid JSON, starting with no users.")
        return set()


def save_users(user_id_set: Set[int]):
    """Saves user IDs to the JSON file."""
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(list(user_id_set), f)
    except IOError as e:
        logger.error(f"Error saving users to {USERS_FILE}: {e}")


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
subscribed_user_ids: Set[int] = load_users()
browser: Optional[Browser] = None
playwright: Optional[Playwright] = None


def format_product_diff_message(
    site_name_md: str, added: Set[str], removed: Set[str]
) -> str:
    """Formats a message showing only what was added or removed from stock."""
    parts: list[str] = []
    if added:
        parts.append(f"🟢 Now in stock: {', '.join(sorted(list(added)))}")
    if removed:
        parts.append(f"🔴 Out of stock: {', '.join(sorted(list(removed)))}")

    if not parts:
        return f"No changes in stock for {site_name_md}."
    return f"{site_name_md}\n" + "\n".join(parts)


async def fetch_products_from_site(site_config: Dict[str, Any], page: Page) -> Set[str]:
    """Fetches product names and links from a given site configuration using a shared page."""
    new_products: Set[str] = set()
    site_display_name = site_config.get(
        "site_name_md", site_config.get("url", "Unknown Site")
    )
    try:
        await page.goto(site_config["url"], timeout=60000)
        product_cards = await page.query_selector_all(
            site_config["product_card_selector"]
        )
        for card in product_cards:
            if site_config.get("out_of_stock_filter"):
                out_of_stock_elem = await card.query_selector(
                    site_config["out_of_stock_filter"]
                )
                if out_of_stock_elem:
                    continue

            name_elem = await card.query_selector(site_config["name_selector"])
            href_elem = await card.query_selector(site_config["href_selector"])

            name = ""
            if name_elem:
                name = (await name_elem.inner_text() or "").strip()

            href = ""
            if href_elem:
                href_attr = await href_elem.get_attribute("href")
                if href_attr:
                    href = href_attr.strip()
                    if href and not href.startswith(("http://", "https://")):
                        href = (
                            site_config["base_url"].rstrip("/") + "/" + href.lstrip("/")
                        )

            if name:
                new_products.add(f"[{name}]({href})" if href else name)
    except PlaywrightTimeoutError:
        logger.error(f"Timeout error checking site {site_display_name}.")
    except PlaywrightError as e:
        logger.error(f"Playwright error checking site {site_display_name}: {e}")
    except Exception as e:
        logger.error(
            f"Unexpected error checking site {site_display_name}: {e}", exc_info=True
        )
    return new_products


async def check_all_sites_task():
    """Checks all sites sequentially using a single page."""
    global browser
    if browser is None:
        logger.error("Browser is not initialized. Aborting check_all_sites_task.")
        return

    page = await browser.new_page()
    try:
        while True:
            for site_key, config in SITES_CONFIG.items():
                logger.info(f"Checking products for {site_key}...")
                fetched_products = await fetch_products_from_site(config, page)

                prev_products = config["current_products"]
                added = fetched_products - prev_products
                removed = prev_products - fetched_products

                if added or removed:
                    logger.info(f"Product change detected for {site_key}.")
                    config["current_products"] = fetched_products

                    if not subscribed_user_ids:
                        logger.info(
                            f"No users subscribed, not sending notifications for {site_key}."
                        )
                        continue

                    message = format_product_diff_message(
                        config["site_name_md"], added, removed
                    )
                    for user_id in list(subscribed_user_ids):
                        try:
                            user = await bot.fetch_user(user_id)
                            await user.send(message)
                        except discord.NotFound:
                            logger.warning(
                                f"User {user_id} not found. Removing from subscriptions."
                            )
                            subscribed_user_ids.discard(user_id)
                            save_users(subscribed_user_ids)
                        except discord.Forbidden:
                            logger.warning(
                                f"Cannot send DM to user {user_id}. They might have DMs disabled or blocked the bot."
                            )
                        except Exception as e:
                            logger.error(
                                f"Error sending message to user {user_id}: {e}",
                                exc_info=True,
                            )
                else:
                    logger.info(f"No product changes for {site_key}.")

            next_run_time = discord.utils.utcnow() + datetime.timedelta(
                seconds=CHECK_INTERVAL_SECONDS
            )
            logger.info(
                f"All sites checked. Next check at {next_run_time.isoformat()}. Sleeping for {CHECK_INTERVAL_SECONDS}s."
            )
            await discord.utils.sleep_until(next_run_time)
    except Exception as e:
        logger.error(f"Unhandled error in check_all_sites_task: {e}", exc_info=True)
    finally:
        logger.info("Closing page used by check_all_sites_task.")
        await page.close()


@bot.tree.command(name="subscribe", description="Subscribe to product notifications.")
async def subscribe(interaction: discord.Interaction) -> None:
    """Adds the user to the subscription list."""
    if interaction.user.id not in subscribed_user_ids:
        subscribed_user_ids.add(interaction.user.id)
        save_users(subscribed_user_ids)
        await interaction.response.send_message(
            "You are now subscribed to product notifications.", ephemeral=True
        )
        logger.info(f"User {interaction.user.id} subscribed.")
    else:
        await interaction.response.send_message(
            "You are already subscribed.", ephemeral=True
        )


@bot.tree.command(
    name="unsubscribe", description="Unsubscribe from product notifications."
)
async def unsubscribe(interaction: discord.Interaction) -> None:
    """Removes the user from the subscription list."""
    if interaction.user.id in subscribed_user_ids:
        subscribed_user_ids.discard(interaction.user.id)
        save_users(subscribed_user_ids)
        await interaction.response.send_message(
            "You have been unsubscribed from product notifications.", ephemeral=True
        )
        logger.info(f"User {interaction.user.id} unsubscribed.")
    else:
        await interaction.response.send_message(
            "You are not currently subscribed.", ephemeral=True
        )


@bot.event
async def on_ready():
    """Called when the bot is ready."""
    global browser, playwright
    logger.info(f"{bot.user} has connected to Discord!")
    try:
        await bot.tree.sync()
        logger.info("Command tree synced.")

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        logger.info("Playwright browser launched successfully.")

        bot.loop.create_task(check_all_sites_task())
        logger.info("Started sequential product check task for all sites.")
    except Exception as e:
        logger.critical(
            f"Fatal error during on_ready initialization: {e}", exc_info=True
        )
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


@bot.event
async def on_disconnect():
    """Called when the bot disconnects."""
    global browser, playwright
    logger.info("Bot is disconnecting...")
    if browser:
        try:
            await browser.close()
            logger.info("Playwright browser closed.")
        except Exception as e:
            logger.error(f"Error closing browser: {e}", exc_info=True)
    if playwright:
        try:
            await playwright.stop()
            logger.info("Playwright stopped.")
        except Exception as e:
            logger.error(f"Error stopping Playwright: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discord bot for product stock monitoring."
    )
    parser.add_argument("--token", type=str, required=True, help="Discord bot token.")
    args = parser.parse_args()

    if not args.token:
        logger.critical("Discord bot token not provided. Exiting.")
        exit(1)

    try:
        bot.run(args.token)
    except discord.LoginFailure:
        logger.critical(
            "Failed to log in with the provided Discord token. Check the token."
        )
    except Exception as e:
        logger.critical(f"An error occurred while running the bot: {e}", exc_info=True)
