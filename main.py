import argparse
import datetime
import json
from typing import Set, Dict, Any

import discord
from discord.ext import commands
from playwright.async_api import Playwright, async_playwright, Browser, Page

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
    },
    "ippodo_us": {
        "url": "https://ippodotea.com/collections/matcha",
        "product_card_selector": "div.matcha-card",
        "out_of_stock_filter": "button.btn-unavailable",
        "name_selector": ".product-title a",
        "href_selector": ".product-title a",
        "base_url": "https://ippodotea.com",
        "site_name_md": "[Ippodo US](https://ippodotea.com/collections/matcha)",
    },
    "marukyu_koyamaen": {
        "url": "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha",
        "product_card_selector": "li.instock",
        "out_of_stock_filter": None,
        "name_selector": ".product-name h4",
        "href_selector": "a.woocommerce-loop-product__link",
        "base_url": "https://www.marukyu-koyamaen.co.jp",
        "site_name_md": "[Marukyu Koyamaen](https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha)",
    },
}


def load_users() -> Set[int]:
    """Loads user IDs from the JSON file."""
    try:
        with open(USERS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_users(user_id_set: Set[int]):
    """Saves user IDs to the JSON file."""
    with open(USERS_FILE, "w") as f:
        json.dump(list(user_id_set), f)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
subscribed_user_ids: Set[int] = load_users()
browser: Browser | None = None
playwright: Playwright | None = None


def format_product_diff_message(
    site_name_md: str, added: Set[str], removed: Set[str]
) -> str:
    """Formats a message showing only what was added or removed from stock."""
    parts: list[str] = []
    if added:
        parts.append(f"🟢 Now in stock: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"🔴 Out of stock: {', '.join(sorted(removed))}")
    if not parts:
        return f"No changes in stock for {site_name_md}."
    return f"{site_name_md}\n" + "\n".join(parts)


async def fetch_products_from_site(site_config: Dict[str, Any], page: Page) -> Set[str]:
    """Fetches product names and links from a given site configuration using a shared page."""
    new_products: Set[str] = set()
    try:
        await page.goto(site_config["url"], timeout=60000)
        product_cards = await page.query_selector_all(
            site_config["product_card_selector"]
        )
        for card in product_cards:
            if site_config.get("out_of_stock_filter"):
                out_of_stock = await card.query_selector(
                    site_config["out_of_stock_filter"]
                )
                if out_of_stock:
                    continue
            name_elem = await card.query_selector(site_config["name_selector"])
            href_elem = await card.query_selector(site_config["href_selector"])
            if name_elem and href_elem:
                name = (await name_elem.inner_text() or "").strip()
                href = (await href_elem.get_attribute("href") or "").strip()
                if href and not href.startswith("http"):
                    href = site_config["base_url"] + href
                if name:
                    new_products.add(f"[{name}]({href})" if href else name)
    except Exception as e:
        print(
            f"Error checking site {site_config.get('site_name_md', 'Unknown Site')}: {e}"
        )
    return new_products


async def check_all_sites_task():
    """Checks all sites sequentially using a single page."""
    global browser
    if browser is None:
        raise RuntimeError(
            "Browser is not initialized. Ensure on_ready is called first."
        )
    page = await browser.new_page()
    try:
        while True:
            for site_key, config in SITES_CONFIG.items():
                print(f"Checking products for {site_key}...")
                fetched_products = await fetch_products_from_site(config, page)
                prev_products = config.get("current_products", set())
                added = fetched_products - prev_products
                removed = prev_products - fetched_products

                if added or removed:
                    print(f"Product change detected for {site_key}.")
                    config["current_products"] = fetched_products

                    if not subscribed_user_ids:
                        print(
                            f"No users subscribed, not sending notifications for {site_key}."
                        )
                        continue

                    message = format_product_diff_message(
                        config["site_name_md"], added, removed
                    )
                    for user_id in subscribed_user_ids:
                        try:
                            user = await bot.fetch_user(user_id)
                            await user.send(message)
                        except discord.NotFound:
                            print(
                                f"User {user_id} not found. Removing from subscriptions."
                            )
                            subscribed_user_ids.discard(user_id)
                            save_users(subscribed_user_ids)
                        except discord.Forbidden:
                            print(
                                f"Cannot send DM to user {user_id}. They might have DMs disabled or blocked the bot."
                            )
                        except Exception as e:
                            print(f"Error sending message to user {user_id}: {e}")
                else:
                    print(f"No product changes for {site_key}.")
            await discord.utils.sleep_until(
                discord.utils.utcnow()
                + datetime.timedelta(seconds=CHECK_INTERVAL_SECONDS)
            )
    finally:
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
    else:
        await interaction.response.send_message(
            "You are not currently subscribed.", ephemeral=True
        )


@bot.event
async def on_ready():
    """Called when the bot is ready."""
    global browser
    global playwright
    print(f"{bot.user} has connected to Discord!")
    await bot.tree.sync()
    print("Command tree synced.")

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)

    # Start the single sequential check task
    bot.loop.create_task(check_all_sites_task())
    print("Started sequential product check task for all sites.")


@bot.event
async def on_disconnect():
    """Called when the bot disconnects."""
    print("Bot is disconnecting...")
    if browser:
        await browser.close()
    if playwright:
        await playwright.stop()
    print("Browser and Playwright closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discord bot for product stock monitoring."
    )
    parser.add_argument("--token", type=str, required=True, help="Discord bot token.")
    args = parser.parse_args()
    bot.run(args.token)
