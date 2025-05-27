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
products: set[str] = set()


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
                    name = await name_elem.inner_text()
                    new_products.add(name.strip())
    if new_products != products:
        products.update(new_products)
        for user_id in user_ids:
            user = await bot.fetch_user(user_id)
            await user.send(
                f"On the ippodo global website, product(s) `{', '.join([product for product in products])}` are in stock."
            )


@bot.tree.command(name="subscribe", description="Become a subscribed user")
async def subscribe(interaction: discord.Interaction) -> None:
    user_ids.add(interaction.user.id)
    save_users()
    await interaction.response.send_message("You are now a subscribed user.", ephemeral=True)


@bot.tree.command(name="unsubscribe", description="Stop being a subscribed user")
async def unsubscribe(interaction: discord.Interaction) -> None:
    user_ids.remove(interaction.user.id)
    save_users()
    await interaction.response.send_message("You are no longer a subscribed user.", ephemeral=True)


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
