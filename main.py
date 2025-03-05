import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import os
import sys
import argparse

from selenium import webdriver
from selenium.webdriver.common.by import By

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
user_watchlist: dict[int, set[str]] = {}


def check_course(course_code: str) -> str:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get("https://www.reg.uci.edu/cgi-bin/WebSoc")
        course_input = driver.find_element(By.NAME, "CourseCodes")
        course_input.clear()
        course_input.send_keys(course_code)
        submit_button = driver.find_element(
            By.XPATH, '//input[@type="submit" and @value="Display Text Results"]'
        )
        submit_button.click()
        time.sleep(3)
        pre_element = driver.find_element(By.TAG_NAME, "pre")
        content = pre_element.text
    except Exception as e:
        print(f"Error while checking course {course_code}: {e}")
        content = ""
    finally:
        driver.quit()
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
        await interaction.response.send_message(
            f"Added course `{course_code}` to your watch list.", ephemeral=True
        )


@bot.tree.command(name="remove", description="Remove a course from your watch list")
@app_commands.describe(course_code="The course code to remove")
async def remove(interaction: discord.Interaction, course_code: str):
    user_id = interaction.user.id
    if user_id in user_watchlist and course_code in user_watchlist[user_id]:
        user_watchlist[user_id].remove(course_code)
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


@tasks.loop(hours=1)
async def check_courses():
    for user_id, courses in user_watchlist.items():
        for course_code in courses:
            try:
                content = await asyncio.to_thread(check_course, course_code)
                if content and "FULL" not in content:
                    user = bot.get_user(user_id)
                    if user:
                        try:
                            await user.send(
                                f"Good news! Course `{course_code}` appears to have an open spot:\n```{content}```"
                            )
                        except Exception as e:
                            print(f"Failed to send message to user {user_id}: {e}")
            except Exception as e:
                print(f"Error checking course {course_code} for user {user_id}: {e}")


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
