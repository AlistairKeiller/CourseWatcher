import argparse

import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
user_ids: set[int] = set()


@tasks.loop(minutes=1)
async def check_ippodo_global():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://global.ippodo-tea.co.jp/collections/matcha")
        product_cards = await page.query_selector_all("li.m-product-card")
        products: list[str] = []
        for card in product_cards:
            button = await card.query_selector('button:has-text("Add to Cart")')
            if button:
                name_elem = await card.query_selector(".m-product-card__name a")
                if name_elem:
                    name = await name_elem.inner_text()
                    products.append(name.strip())
        if products:
            for user_id in user_ids:
                user = await bot.fetch_user(user_id)
                await user.send(
                    f"On the ippodo global website (https://global.ippodo-tea.co.jp), product(s) `{', '.join([product for product in products])}` are in stock."
                )


@bot.tree.command(name="subscribe", description="Become a subscribed user")
async def subscribe(interaction: discord.Interaction) -> None:
    user_ids.add(interaction.user.id)
    await interaction.response.send_message("You are now a subscribed user.", ephemeral=True)


@bot.tree.command(name="unsubscribe", description="Stop being a subscribed user")
async def unsubscribe(interaction: discord.Interaction) -> None:
    user_ids.remove(interaction.user.id)
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
