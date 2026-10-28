import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncpg
import datetime
from math import ceil

WEEKDAY = 0  # monday


class Management(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool = bot.db

    async def cog_load(self):
        self.weekly_deduction.start()
        print(f'Cog "{self.__class__.__name__}" loaded.')

    async def cog_unload(self):
        self.weekly_deduction.cancel()
        print(f'Cog "{self.__class__.__name__}" unloaded.')

    async def _handle_channels(self, conn: asyncpg.Connection, weeks: int=1) -> tuple:
        amount = 0
        if weeks > 0:
            result = await conn.execute("""
                UPDATE channels
                SET health = health - $1;
            """, weeks)
            amount = int(result.split()[-1])
        records = await conn.fetch("""
            SELECT id
            FROM channels
            WHERE health < 1;
        """)

        count = 0
        for row in records:
            id = row[0]
            try:
                channel = (self.bot.get_channel(id)
                    or await self.bot.fetch_channel(id))
                try:
                    await channel.delete()
                    count += 1
                except Exception as e:
                    print(f'#{channel.name}: {e}')
            except Exception as e:
                print(f'ID {id}: {e}')
        await conn.execute("""
            DELETE FROM channels
            WHERE health < 1;
        """)
        print(amount, count)
        return amount, count


    @tasks.loop(time=datetime.time(
        hour=0,
        tzinfo=datetime.timezone.utc
    ))
    async def weekly_deduction(self):
        today = datetime.date.today()
        if today.weekday() != WEEKDAY: return

        async with self.db.acquire() as conn:
            async with conn.transaction():
                last = await conn.fetchval("""
                    SELECT last
                    FROM bot_state
                    FOR UPDATE;
                """)
                weeks = ceil((today - last).days / 7)
                updated, removed = await self._handle_channels(conn, weeks)
                await conn.execute("""
                    UPDATE bot_state
                    SET last = NOW();
                """)
                print(f'Updated {updated}, removed {removed}')


    @weekly_deduction.before_loop
    async def before_deduction(self):
        await self.bot.wait_until_ready()

    @commands.command(aliases=('init',))
    async def initialize(self, ctx):
        """
        All the functions in this cog
        do not have a check if the channel exists or not.
        Please initialize before doing anything
        """
        entries = {}
        async for entry in ctx.guild.audit_logs(
            action=discord.AuditLogAction.channel_create,
            limit=None
        ):
            if not entry.target or not entry.user or entry.user.bot: continue
            type = getattr(entry.after, 'type', None)
            if not type or type == discord.ChannelType.category: continue

            entries[entry.target.id] = entry.user.id

        records = []
        for channel in ctx.guild.channels:
            if isinstance(channel, discord.CategoryChannel): continue

            if channel.id in entries:
                records.append((channel.id, entries[channel.id]))
            else:
                records.append((channel.id, ctx.guild.owner_id))

        async with self.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TEMP TABLE staging
                    (LIKE channels INCLUDING DEFAULTS)
                    ON COMMIT DROP;
                """)
                await conn.copy_records_to_table(
                    table_name='staging',
                    records=records,
                    columns=('id', 'owner')
                )
                result = await conn.execute("""
                    INSERT INTO channels (id, owner)
                    SELECT id, owner FROM staging
                    ON CONFLICT (id) DO NOTHING;
                """)

                amount = int(result.split()[-1])
                if amount:
                    await ctx.reply(f'Added {amount} channel(s)')
                else:
                    await ctx.reply('No new channels added')


    @commands.command(aliases=('deduct',))
    async def force_deduction(self, ctx, weeks: int=1):
        async with self.db.acquire() as conn:
            async with conn.transaction():
                updated, removed = await self._handle_channels(conn, weeks)
                await ctx.reply(f'Updated {updated}, removed {removed} channel(s)')


    @app_commands.command(description='Check channel health')
    async def check(self, inter, channel: app_commands.AppCommandChannel=None):
        channel = channel or inter.channel
        if isinstance(channel, discord.CategoryChannel):
            await inter.response.send_message("This is a category, we don't serve categories", ephemeral=True)
            return

        owner_id, health = await self.db.fetchrow("""
            SELECT owner, health
            FROM channels
            WHERE id = $1;
        """, channel.id)
        owner = (inter.guild.get_member(owner_id)
            or await inter.guild.fetch_member(owner_id))
        await inter.response.send_message(f'{channel.mention} is at {health} health\nOwned by {owner.mention}')


    @app_commands.command(description='Transfer health to target channel, defaults to all health')
    async def transfer(self, inter, target: app_commands.AppCommandChannel, amount: int=(-1), source: app_commands.AppCommandChannel=None):
        source = source or inter.channel
        if isinstance(target, discord.CategoryChannel) or isinstance(source, discord.CategoryChannel):
            await inter.response.send_message("This is a category, we don't serve categories", ephemeral=True)
            return

        async with self.db.acquire() as conn:
            async with conn.transaction():
                owner_id, health = await conn.fetchrow("""
                    SELECT owner, health
                    FROM channels
                    WHERE id = $1
                    FOR UPDATE;
                """, source.id)
                if owner_id != inter.user.id:
                    await inter.response.send_message("You don't own this channel", ephemeral=True)
                    return
                if amount < 0:
                    amount = health
                elif amount > health:
                    await inter.response.send_message('Amount too high, please check your channel health', ephemeral=True)
                    return

                await self.db.execute("""
                    UPDATE channels
                    SET health = health + $2
                    WHERE id = $1;
                """, target.id, amount)
                await self.db.execute("""
                    UPDATE channels
                    SET health = health - $2
                    WHERE id = $1;
                """, source.id, amount)
                async with conn.transaction():
                    await self._handle_channels(conn, 0)
                    await inter.response.send_message('Successfully transferred health to {target.mention}')


    @app_commands.command(description='Transfers channel ownership to someone else')
    async def move(self, inter, new_owner: discord.Member, channel: app_commands.AppCommandChannel=None):
        channel = channel or inter.channel
        if isinstance(channel, discord.CategoryChannel):
            await inter.response.send_message("This is a category, we don't serve categories", ephemeral=True)
            return
        if new_owner == inter.user:
            await inter.response.send_message('Transfer to another member, please', ephemeral=True)
            return
        elif new_owner.bot:
            await inter.response.send_message("Bots don't count", ephemeral=True)
            return

        owner_id = await self.db.fetchval("""
            SELECT owner
            FROM channels
            WHERE id = $1;
        """, channel.id)
        if owner_id != inter.user.id:
            await inter.response.send_message("You don't own this channel", ephemeral=True)
            return

        # should add a confirmation prompt, not sure if it's necessary
        await self.db.execute("""
            UPDATE channels
            SET owner = $2
            WHERE id = $1;
        """, channel.id, new_owner.id)
        await inter.response.send_message(f'Successfully transferred ownership to {new_owner.mention}')


async def setup(bot: commands.Bot):
    await bot.add_cog(Management(bot))
