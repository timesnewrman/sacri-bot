import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
import csv
import json

PATH = 'potions.csv'


class Items(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool = bot.db

    async def cog_load(self):
        print(f'Cog "{self.__class__.__name__}" loaded.')

    async def cog_unload(self):
        print(f'Cog "{self.__class__.__name__}" unloaded.')

    @commands.command()
    async def test(self, ctx):
        with open(PATH, mode='r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)

            await ctx.reply(json.dumps([str(i) for i in reader], indent=2, default=str) or 'None')


async def setup(bot: commands.Bot):
    await bot.add_cog(Items(bot))
