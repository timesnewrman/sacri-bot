import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import asyncpg
import os
import json

load_dotenv()


class Tree(app_commands.CommandTree):
    async def on_error(self, inter, error):
        if isinstance(error, app_commands.CheckFailure): return

        try:
            await inter.response.send_message(error+'\n\nPlease contact a referee for additional information', ephemeral=True)
        except:
            pass
        await super().on_error(inter, error)

class Connoisseur(commands.Bot):
    def __init__(self, **kwargs):
        self.test_guild = discord.Object(kwargs.pop('test_guild'))
        self.db: asyncpg.Pool | None = None
        super().__init__(**kwargs)

    async def on_command_error(self, ctx, error):
        if isinstance(error, (
            commands.CheckFailure,
            commands.CommandNotFound,
            commands.DisabledCommand
        )): return

        try:
            await ctx.reply(error)
        except:
            pass
        await super().on_command_error(ctx, error)

    async def setup_hook(self):
        self.db = await asyncpg.create_pool(
            host=os.getenv('POSTGRESQL_HOST', 'localhost'),
            port=os.getenv('POSTGRESQL_PORT', 5432),
            user='connoisseur',
            password=os.environ['POSTGRESQL_PASSWORD'],
            database='sacribot'
        )
        for filename in os.listdir('cogs'):
            if filename.endswith('.py'):
                await self.load_extension('cogs.'+filename[:-3])
        self.tree.copy_global_to(guild=self.test_guild)


bot = Connoisseur(
    command_prefix='!',
    owner_ids=[694912475753349141, 902371374033670224],
    test_guild=1122812983626702949,
    tree_cls=Tree,
    help_command=None,
    intents=discord.Intents(
        guilds=True,
        members=True,
        guild_messages=True,
        moderation=True,
        message_content=True
    ),
    allowed_mentions=discord.AllowedMentions(
        replied_user=False,
        users=False
    )
)

@bot.event
async def on_connect():
    print('Connected')

@bot.event
async def on_ready():
    print('Ready')

@bot.check
async def is_owner(ctx):
    return ctx.author.id in bot.owner_ids

@bot.command()
async def ping(ctx):
    await ctx.reply(f'Pong! {int(bot.latency*1000)}ms')

@bot.command()
async def sync(ctx):
    await bot.tree.sync(guild=bot.test_guild)
    await ctx.reply('Synced')

@bot.command()
async def query(ctx, *, prompt):
    records = await bot.db.fetch(prompt)
    data = [dict(i) for i in records]
    await ctx.reply(json.dumps(data, indent=2, default=str))

@bot.command(aliases=('exec',))
async def execute(ctx, *, prompt):
    result = await bot.db.execute(prompt)
    await ctx.reply(result or 'None')

@bot.command(aliases=('eval',))
async def evaluate(ctx, *, expression):
    if expression.startswith('await '):
        result = await eval(expression[6:])
    else:
        result = eval(expression)
    await ctx.reply(result or 'Done')

@bot.tree.command(name='ping', description='Shows bot latency')
async def slashping(inter):
    await inter.response.send_message(f'Pong! {int(bot.latency*1000)}ms')

bot.run(os.environ['DISCORD_BOT_TOKEN'])
