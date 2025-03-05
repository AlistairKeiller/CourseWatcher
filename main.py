import argparse
import json
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

WATCHLIST_FILE = "watchlist.json"


def load_watchlist() -> dict[int, set[str]]:
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r") as f:
                data = json.load(f)
            return {int(k): set(v) for k, v in data.items()}
        except Exception as e:
            print(f"Error loading watchlist: {e}")
    return {}


def save_watchlist():
    try:
        serializable = {str(k): list(v) for k, v in user_watchlist.items()}
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(serializable, f)
    except Exception as e:
        print(f"Error saving watchlist: {e}")


user_watchlist: dict[int, set[str]] = load_watchlist()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


async def check_course(course_code: str) -> str:
    content = ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("https://www.reg.uci.edu/cgi-bin/WebSoc")
            await page.fill("input[name='CourseCodes']", course_code)
            await page.click("input[type='submit'][value='Display Text Results']")
            content = await page.inner_text("pre")
            await browser.close()
    except Exception as e:
        print(f"Error while checking course {course_code}: {e}")
    return content


@bot.tree.command(name="watch", description="Add a course to watch")
@app_commands.describe(course_code="The course code to watch")
async def watch(interaction: discord.Interaction, course_code: str):
    user_id = interaction.user.id
    if user_id not in user_watchlist:
        user_watchlist[user_id] = set()

    if course_code in user_watchlist[user_id]:
        await interaction.response.send_message(
            f"You are already watching course `{course_code}`.", ephemeral=True
        )
    else:
        user_watchlist[user_id].add(course_code)
        save_watchlist()
        await interaction.response.send_message(
            f"Added course `{course_code}` to your watch list.", ephemeral=True
        )


@bot.tree.command(name="remove", description="Remove a course from your watch list")
@app_commands.describe(course_code="The course code to remove")
async def remove(interaction: discord.Interaction, course_code: str):
    user_id = interaction.user.id
    if user_id in user_watchlist and course_code in user_watchlist[user_id]:
        user_watchlist[user_id].remove(course_code)
        save_watchlist()
        await interaction.response.send_message(
            f"Removed course `{course_code}` from your watch list.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Course `{course_code}` is not in your watch list.", ephemeral=True
        )


@bot.tree.command(name="list", description="List your current watch list")
async def list_courses(interaction: discord.Interaction):
    user_id = interaction.user.id
    courses = user_watchlist.get(user_id, set())
    if courses:
        courses_str = ", ".join(sorted(courses))
        await interaction.response.send_message(
            f"Your current watch list: {courses_str}", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "Your watch list is empty.", ephemeral=True
        )


async def run_check_courses():
    for user_id, courses in user_watchlist.items():
        for course_code in courses:
            try:
                content = await check_course(course_code)
                if content and "FULL" not in content:
                    try:
                        user = await bot.fetch_user(user_id)
                        if user:
                            await user.send(
                                f"Good news! Course `{course_code}` appears to have an open spot."
                            )
                    except Exception as e:
                        print(f"Failed to send message to user {user_id}: {e}")

            except Exception as e:
                print(f"Error checking course {course_code} for user {user_id}: {e}")


@tasks.loop(minutes=10)
async def check_courses():
    await run_check_courses()


@bot.tree.command(name="check_courses", description="Manually trigger a course check")
async def check_courses_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await run_check_courses()
    await interaction.followup.send("Course check completed.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)
    check_courses.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discord Bot Token")
    parser.add_argument("--token", type=str, help="Discord Bot Token")
    args = parser.parse_args()

    token = args.token or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print(
            "Error: Discord token not provided. Use --token or set the DISCORD_BOT_TOKEN environment variable."
        )
        sys.exit(1)

    bot.run(token)
