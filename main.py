import argparse
import json
from typing import Set, Dict, Any

import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

# --- Configuration ---
USERS_FILE = "users.json"
CHECK_INTERVAL_SECONDS = 60  # Interval in seconds for checking product stock

# Site configurations
SITES_CONFIG: Dict[str, Dict[str, Any]] = {
    "ippodo_global": {
        "url": "https://global.ippodo-tea.co.jp/collections/matcha",
        "product_card_selector": "li.m-product-card",
        "add_to_cart_selector": 'button:has-text("Add to Cart")',
        "name_selector": ".m-product-card__name a",
        "base_url": "https://global.ippodo-tea.co.jp",
        "site_name_md": "[Ippodo Global](https://global.ippodo-tea.co.jp/collections/matcha)",
        "current_products": set(),
    },
    "ippodo_us": {
        "url": "https://ippodotea.com/collections/matcha",
        "product_card_selector": "div.matcha-card",
        "add_to_cart_selector": 'button:has-text("Add to bag")',
        "name_selector": ".product-title a",
        "base_url": "https://ippodotea.com",
        "site_name_md": "[Ippodo US](https://ippodotea.com/collections/matcha)",
        "current_products": set(),
    },
    "marukyu_koyamaen": {
        "url": "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha",
        "product_card_selector": "li.instock",
        "add_to_cart_selector": "a.woocommerce-loop-product__link",
        "name_selector": "a.woocommerce-loop-product__link",
        "name_source_attribute": "title",  # Get product name from the 'title' attribute
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
        return set()


def save_users(user_id_set: Set[int]):
    """Saves user IDs to the JSON file."""
    with open(USERS_FILE, "w") as f:
        json.dump(list(user_id_set), f)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
subscribed_user_ids: Set[int] = load_users()


def format_product_message(site_name_md: str, products: Set[str]) -> str:
    """Formats the message to send to users about product stock."""
    if not products:
        return f"On the {site_name_md} website, no products are currently in stock."
    product_list_str = ", ".join(sorted(list(products)))
    if len(products) == 1:
        return f"On the {site_name_md} website, product {product_list_str} is in stock."
    return f"On the {site_name_md} website, products {product_list_str} are in stock."


async def fetch_products_from_site(site_config: Dict[str, Any]) -> Set[str]:
    """Fetches product names and links from a given site configuration."""
    new_products: Set[str] = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(site_config["url"], timeout=30000)
            product_cards = await page.query_selector_all(
                site_config["product_card_selector"]
            )
            for card in product_cards:
                add_to_cart_button = await card.query_selector(
                    site_config["add_to_cart_selector"]
                )
                if add_to_cart_button and await add_to_cart_button.is_visible():
                    name_elem = await card.query_selector(site_config["name_selector"])
                    if name_elem:
                        name = ""
                        name_attribute_source = site_config.get("name_source_attribute")
                        if name_attribute_source:
                            name = (
                                await name_elem.get_attribute(name_attribute_source)
                                or ""
                            ).strip()

                        if (
                            not name
                        ):  # Fallback to inner_text if attribute not found or empty
                            name = (await name_elem.inner_text() or "").strip()

                        link_attr = (
                            await name_elem.get_attribute("href") or ""
                        ).strip()
                        full_link = ""
                        if link_attr:
                            if link_attr.startswith("http"):
                                full_link = link_attr
                            elif site_config["base_url"]:
                                base = site_config["base_url"].rstrip("/")
                                relative = link_attr.lstrip("/")
                                full_link = f"{base}/{relative}"

                        if name:  # Ensure name is not empty
                            new_products.add(
                                f"[{name}]({full_link})" if full_link else name
                            )
        except Exception as e:
            print(
                f"Error checking site {site_config.get('site_name_md', 'Unknown Site')}: {e}"
            )
        finally:
            await browser.close()
    return new_products


def create_product_check_task(site_key: str, config: Dict[str, Any]):
    """Factory to create a specific product checking task for a site."""

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def _check_products_task():
        print(f"Checking products for {site_key}...")
        fetched_products = await fetch_products_from_site(config)

        if not isinstance(
            config["current_products"], set
        ):  # Should always be a set from init
            config["current_products"] = set()

        if fetched_products != config["current_products"]:
            print(f"Product change detected for {site_key}.")
            config["current_products"].clear()
            config["current_products"].update(fetched_products)

            if not subscribed_user_ids:
                print(f"No users subscribed, not sending notifications for {site_key}.")
                return

            message = format_product_message(
                config["site_name_md"],
                config["current_products"],
            )
            for user_id in list(
                subscribed_user_ids
            ):  # Iterate over a copy for safe modification
                try:
                    user = await bot.fetch_user(user_id)
                    await user.send(message)
                except discord.NotFound:
                    print(f"User {user_id} not found. Removing from subscriptions.")
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

    _check_products_task.__name__ = f"check_{site_key}_products_task"
    return _check_products_task


# --- Bot Commands ---
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


# --- Bot Events ---
@bot.event
async def on_ready():
    """Called when the bot is ready."""
    print(f"{bot.user} has connected to Discord!")
    await bot.tree.sync()
    print("Command tree synced.")

    for site_key, config_item in SITES_CONFIG.items():
        task = create_product_check_task(site_key, config_item)
        task.start()
        print(f"Started product check task for {site_key}.")
    print("All product check tasks started.")


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discord bot for product stock monitoring."
    )
    parser.add_argument("--token", type=str, required=True, help="Discord bot token.")
    args = parser.parse_args()
    bot.run(args.token)
