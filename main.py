import argparse
import json

import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright


def load_users() -> set[int]:
    try:
        with open("users.json") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.decoder.JSONDecodeError):
        return set()


def save_users():
    with open("users.json", "w") as f:
        json.dump(list(user_ids), f)


bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
user_ids: set[int] = load_users()
ippodo_global_products: set[str] = set()
ippodo_products: set[str] = set()


def format_product_message(site: str, products: set[str]) -> str:
    if not products:
        return f"On the {site} website, no products are currently in stock."
    elif len(products) == 1:
        return f"On the {site} website, product {next(iter(products))} is in stock."
    else:
        return f"On the {site} website, products {', '.join(products)} are in stock."


@tasks.loop(seconds=20)
async def check_ippodo_global():
    new_products: set[str] = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://global.ippodo-tea.co.jp/collections/matcha")
        product_cards = await page.query_selector_all("li.m-product-card")
        for card in product_cards:
            button = await card.query_selector('button:has-text("Add to Cart")')
            if button:
                name_elem = await card.query_selector(".m-product-card__name a")
                if name_elem:
                    name = (await name_elem.inner_text()).strip()
                    link = (await name_elem.get_attribute("href") or "").strip()
                    new_products.add(f"[{name}]({link})" if link else name)
    if new_products != ippodo_global_products:
        ippodo_global_products.clear()
        ippodo_global_products.update(new_products)
        for user_id in user_ids:
            user = await bot.fetch_user(user_id)
            await user.send(
                format_product_message(
                    "[ippodo global](https://global.ippodo-tea.co.jp/collections/matcha)",
                    ippodo_global_products,
                )
            )


@tasks.loop(seconds=20)
async def check_ippodo():
    new_products: set[str] = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://ippodotea.com/collections/matcha")
        product_cards = await page.query_selector_all("div.matcha-card")
        for card in product_cards:
            button = await card.query_selector('button:has-text("Add to bag")')
            if button:
                name_elem = await card.query_selector(".product-title a")
                if name_elem:
                    name = (await name_elem.inner_text()).strip()
                    link = (await name_elem.get_attribute("href") or "").strip()
                    new_products.add(f"[{name}]({link})" if link else name)
    if new_products != ippodo_products:
        ippodo_products.clear()
        ippodo_products.update(new_products)
        for user_id in user_ids:
            user = await bot.fetch_user(user_id)
            await user.send(
                format_product_message(
                    "[ippodo](https://ippodotea.com/collections/matcha)",
                    ippodo_products,
                )
            )


@bot.tree.command(name="subscribe", description="Become a subscribed user")
async def subscribe(interaction: discord.Interaction) -> None:
    user_ids.add(interaction.user.id)
    save_users()
    await interaction.response.send_message(
        "You are now a subscribed user.", ephemeral=True
    )


@bot.tree.command(name="unsubscribe", description="Stop being a subscribed user")
async def unsubscribe(interaction: discord.Interaction) -> None:
    user_ids.remove(interaction.user.id)
    save_users()
    await interaction.response.send_message(
        "You are no longer a subscribed user.", ephemeral=True
    )


@bot.event
async def on_ready():
    await bot.tree.sync()
    check_ippodo_global.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", type=str)
    args = parser.parse_args()

    if args.token:
        bot.run(args.token)
    else:
        print("Error: Discord token not provided. Use --token INSERT_TOKEN_HERE.")
