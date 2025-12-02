import discord
from discord.ext import commands, tasks
import psycopg2  # Reemplaza sqlite3
import random
import time
from datetime import datetime, timedelta
from contextlib import contextmanager  # Para un mejor manejo de la conexión
import asyncio
from typing import Optional
import os
from flask import Flask  # Para mantener el bot vivo en Render
import yt_dlp

# ----------------------------------------------------
# 1. YTDL y FFMPEG Opciones
# ----------------------------------------------------

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

music_queues = {}

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

def play_next(ctx):
    if len(music_queues[ctx.guild.id]) > 0:
        source = music_queues[ctx.guild.id].pop(0)
        ctx.voice_client.play(source, after=lambda x: play_next(ctx))
        asyncio.run_coroutine_threadsafe(ctx.send(f"Ahora reproduciendo: **{source.title}**"), bot.loop)
    else:
        asyncio.run_coroutine_threadsafe(ctx.voice_client.disconnect(), bot.loop)

# ----------------------------------------------------
# 1. CLASE DE AYUDA PERSONALIZADA
# ----------------------------------------------------

class CustomHelpCommand(commands.HelpCommand):
    """Clase personalizada para manejar el comando de ayuda (!help, !ayuda)."""


    async def send_bot_help(self, mapping):
        ctx = self.context


        prefix = ctx.prefix 

        embed = discord.Embed(
            title="📚 Guía de Comandos del Bot",
            description=f"Aquí está la lista de comandos disponibles. Recuerda usar el prefijo `{prefix}` para los comandos de prefijo (ej: `{prefix}balance`).",
            color=discord.Color.blurple()
        )


        mod_cmds = (
            "`/mod-ban <user> <razón>`: Banea a un usuario.",
            "`/mod-kick <user> <razón>`: Expulsa a un usuario.",
            "`/mod-mute <user> <duración>`: Silencia temporalmente.",
            "`/mod-warn <user> <razón>`: Aplica una advertencia.",
            "`/mod-purge <cantidad>`: Borra mensajes del canal."
        )
        embed.add_field(name="🛡️ Moderación (Slash)", value='\n'.join(mod_cmds), inline=False)


        eco_cmds = (
            f"`{prefix}balance` o `{prefix}bal`: Muestra tu saldo.",
            f"`{prefix}daily`: Reclama tu recompensa diaria.",
            f"`{prefix}work`: Gana dinero por trabajar.",
            f"`{prefix}flip <cara|cruz> <monto>`: Apuesta a cara o cruz.",
            f"`{prefix}shop`: Ve los roles a la venta.",
            f"`{prefix}buyrole <rol>`: Compra un rol de la tienda.",
            f"`{prefix}rank`: Muestra tu nivel y XP.",
            f"`{prefix}leaderboard` o `{prefix}top`: Muestra la tabla de niveles."
        )
        embed.add_field(name="💰 Economía y Niveles (Prefix)", value='\n'.join(eco_cmds), inline=False)


        marriage_cmds = (
            f"`{prefix}marry <user>`: Propone matrimonio.",
            f"`{prefix}divorce`: Inicia el proceso de divorcio.",
            f"`{prefix}spouse`: Muestra con quién estás casado y desde cuándo."
        )
        embed.add_field(name="❤️ Bodas (Prefix)", value='\n'.join(marriage_cmds), inline=False)


        music_cmds = (
            f"`{prefix}play <canción>` o `{prefix}p`: Reproduce música.",
            f"`{prefix}stop`: Detiene la reproducción y desconecta.",
            f"`{prefix}skip` o `{prefix}s`: Salta la canción actual."
        )
        embed.add_field(name="🎶 Música (Prefix)", value='\n'.join(music_cmds), inline=False)


        util_cmds = (
            "`/report <user> <razón>`: Reporta a un usuario.",
            "`/admin-setlogs <canal>`: Configura el canal de logs.",
            "`/admin-setmoney <user> <monto>`: Establece el saldo de un usuario (Admin).",
            f"`{prefix}sync`: Sincroniza comandos Slash (Owner)."
        )
        embed.add_field(name="🛠️ Utilidad / Admin", value='\n'.join(util_cmds), inline=False)

        embed.set_footer(text=f"Prefix: {prefix} | Desarrollado ReynDev.")

        await ctx.send(embed=embed)


# ----------------------------------------------------
# 2. CONFIGURACIÓN Y CONSTANTES
# ----------------------------------------------------

OWNER_ID = 1224791534436749354
PREFIX = '!'
INTENTS = discord.Intents.all()
MUTE_ROLE_NAME = "Silenciado"
DAILY_REWARD = 500
ECONOMY_COOLDOWN_DAILY_HOURS = 24
ECONOMY_COOLDOWN_WORK_HOURS = 1
XP_PER_MESSAGE = 15
XP_COOLDOWN_SECONDS = 60

DATABASE_URL = os.getenv('DATABASE_URL')

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)
bot.help_command = CustomHelpCommand()

# ----------------------------------------------------
# 3. FUNCIONES DE UTILIDAD PARA EMBEDS
# ----------------------------------------------------

def create_error_embed(title: str, description: str) -> discord.Embed:
    """Crea un embed estándar para mensajes de error."""
    return discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=discord.Color.red()
    )

def create_success_embed(title: str, description: str) -> discord.Embed:
    """Crea un embed estándar para mensajes exitosos."""
    return discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=discord.Color.green()
    )

async def create_role_if_not_exists(guild: discord.Guild, role_name: str) -> discord.Role:
    """Crea un rol con el nombre especificado si no existe, ajustando permisos para MUTE_ROLE_NAME."""
    role = discord.utils.get(guild.roles, name=role_name)
    if role:
        return role

    role = await guild.create_role(name=role_name, permissions=discord.Permissions.none())

    if role_name == MUTE_ROLE_NAME:
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                try:
                    await channel.set_permissions(role, send_messages=False, speak=False)
                except discord.Forbidden:
                    print(f"Advertencia: No se pudieron establecer permisos para el canal {channel.name}.")

    return role


# ----------------------------------------------------
# 4. FUNCIONES DE UTILIDAD DE BASE DE DATOS (DB)
# ----------------------------------------------------

@contextmanager
def get_db_connection():
    """Crea y maneja una conexión a la base de datos PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        yield conn
    finally:
        if conn:
            conn.close()

def initialize_db():
    """Inicializa la base de datos PostgreSQL y crea las tablas necesarias."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    guild_id BIGINT PRIMARY KEY,
                    log_channel_id BIGINT,
                    report_channel_id BIGINT,
                    report_role_id BIGINT,
                    autorole_id BIGINT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS temp_mutes (
                    user_id BIGINT,
                    guild_id BIGINT,
                    unmute_time DOUBLE PRECISION,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    guild_id BIGINT,
                    moderator_id BIGINT,
                    reason TEXT,
                    timestamp TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS economy (
                    user_id BIGINT,
                    guild_id BIGINT,
                    balance INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id BIGINT,
                    guild_id BIGINT,
                    action TEXT,
                    last_time DOUBLE PRECISION,
                    PRIMARY KEY (user_id, guild_id, action)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leveling (
                    user_id BIGINT,
                    guild_id BIGINT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    last_message_time DOUBLE PRECISION DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS role_shop (
                    guild_id BIGINT,
                    role_id BIGINT,
                    price INTEGER,
                    PRIMARY KEY (guild_id, role_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS marriages (
                    user1_id BIGINT,
                    user2_id BIGINT,
                    guild_id BIGINT,
                    marriage_date TEXT,
                    PRIMARY KEY (user1_id, user2_id, guild_id)
                )
            """)
            conn.commit()


def get_config(guild: discord.Guild):
    """Obtiene la configuración del servidor."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT log_channel_id, report_channel_id, report_role_id, autorole_id FROM config WHERE guild_id = %s", (guild.id,))
            result = cursor.fetchone()
            if result:
                return {
                    'log_channel_id': result[0],
                    'report_channel_id': result[1],
                    'report_role_id': result[2],
                    'autorole_id': result[3]
                }
            return {}

def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Obtiene el canal de logs del servidor."""
    config = get_config(guild)
    if config.get('log_channel_id'):
        return guild.get_channel(config['log_channel_id'])
    return None

def get_report_config(guild: discord.Guild) -> tuple[Optional[discord.TextChannel], Optional[discord.Role]]:
    """Obtiene el canal y rol de reportes."""
    config = get_config(guild)
    channel = guild.get_channel(config.get('report_channel_id'))
    role = guild.get_role(config.get('report_role_id'))
    return channel, role


def get_balance(user_id: int, guild_id: int) -> int:
    """Obtiene el saldo de un usuario."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO economy (user_id, guild_id, balance) VALUES (%s, %s, 0) ON CONFLICT (user_id, guild_id) DO NOTHING",
                (user_id, guild_id)
            )
            conn.commit()
            cursor.execute("SELECT balance FROM economy WHERE user_id = %s AND guild_id = %s", (user_id, guild_id))
            result = cursor.fetchone()
            return result[0] if result else 0

def update_balance(user_id: int, guild_id: int, amount: int):
    """Añade o resta una cantidad al saldo de un usuario."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO economy (user_id, guild_id, balance) VALUES (%s, %s, 0) ON CONFLICT (user_id, guild_id) DO NOTHING",
                (user_id, guild_id)
            )
            cursor.execute("UPDATE economy SET balance = balance + %s WHERE user_id = %s AND guild_id = %s", (amount, user_id, guild_id))
            conn.commit()

def set_balance(user_id: int, guild_id: int, amount: int) -> int:
    """Establece el saldo de un usuario a una cantidad específica."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO economy (user_id, guild_id, balance) VALUES (%s, %s, 0) ON CONFLICT (user_id, guild_id) DO NOTHING",
                (user_id, guild_id)
            )
            cursor.execute("UPDATE economy SET balance = %s WHERE user_id = %s AND guild_id = %s", (amount, user_id, guild_id))
            conn.commit()
            return get_balance(user_id, guild_id)


def get_last_action_time(user_id: int, guild_id: int, action: str) -> float:
    """Obtiene la última hora de una acción específica (timestamp)."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT last_time FROM cooldowns WHERE user_id = %s AND guild_id = %s AND action = %s", (user_id, guild_id, action))
            result = cursor.fetchone()
            return result[0] if result else 0.0

def set_last_action_time(user_id: int, guild_id: int, action: str):
    """Establece la última hora de una acción específica al tiempo actual."""
    current_time = time.time()
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cooldowns (user_id, guild_id, action, last_time) VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, guild_id, action) DO UPDATE SET last_time = EXCLUDED.last_time
                """,
                (user_id, guild_id, action, current_time)
            )
            conn.commit()


def get_level_data(user_id: int, guild_id: int) -> tuple[int, int, float]:
    """Obtiene XP, Nivel y el último tiempo de mensaje de un usuario."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO leveling (user_id, guild_id) VALUES (%s, %s) ON CONFLICT (user_id, guild_id) DO NOTHING",
                (user_id, guild_id)
            )
            conn.commit()
            cursor.execute("SELECT xp, level, last_message_time FROM leveling WHERE user_id = %s AND guild_id = %s", (user_id, guild_id))
            result = cursor.fetchone()
            return result if result else (0, 0, 0.0)

def get_xp_needed(level: int) -> int:
    """Calcula el XP necesario para el siguiente nivel."""
    return 100 + level * 50

def update_level_data(user_id: int, guild_id: int, xp_to_add: int, last_message_time: float) -> tuple[int, bool]:
    """Actualiza los datos de XP y Nivel de un usuario. Retorna el nuevo nivel y si subió de nivel."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            xp, level, _ = get_level_data(user_id, guild_id)
            new_xp = xp + xp_to_add
            new_level = level
            leveled_up = False
            xp_needed = get_xp_needed(level)
            while new_xp >= xp_needed:
                new_xp -= xp_needed
                new_level += 1
                leveled_up = True
                xp_needed = get_xp_needed(new_level) 
            cursor.execute(
                "UPDATE leveling SET xp = %s, level = %s, last_message_time = %s WHERE user_id = %s AND guild_id = %s",
                (new_xp, new_level, last_message_time, user_id, guild_id)
            )
            conn.commit()
            return new_level, leveled_up

def get_shop_roles(guild_id: int) -> list[tuple[int, int]]:
    """Obtiene todos los roles a la venta en el servidor."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT role_id, price FROM role_shop WHERE guild_id = %s", (guild_id,))
            return cursor.fetchall()

def get_partner(user_id: int, guild_id: int) -> Optional[int]:
    """Obtiene la ID del compañero de matrimonio."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user2_id, user1_id FROM marriages WHERE (user1_id = %s OR user2_id = %s) AND guild_id = %s", (user_id, user_id, guild_id))
            result = cursor.fetchone()
            if result:
                return result[0] if result[0] != user_id else result[1]
            return None

def get_marriage_data(user_id: int, guild_id: int) -> Optional[tuple[int, int, str]]:
    """Obtiene (user1_id, user2_id, marriage_date) de un matrimonio."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user1_id, user2_id, marriage_date FROM marriages WHERE (user1_id = %s OR user2_id = %s) AND guild_id = %s", (user_id, user_id, guild_id))
            return cursor.fetchone()

CLIENT_ID_INVITE = "1443693871061008457"
PERMISSION_CODE_INVITE = 2422992118

def generate_invite_link(client_id: str, permissions: int) -> str:
    """Genera el enlace de invitación para el bot."""
    return f"https://discord.com/oauth2/authorize?client_id={client_id}&scope=bot%20applications.commands&permissions={permissions}"


@bot.event
async def on_ready():
    """Se ejecuta cuando el bot está listo y conectado a Discord."""
    print(f'Bot conectado como {bot.user.name} (ID: {bot.user.id})')
    initialize_db()
    # La sincronización ahora se maneja manualmente con el comando /sync.
    # try:
    #     await bot.tree.sync()
    #     print("Comandos Slash sincronizados exitosamente.")
    # except Exception as e:
    #     print(f"Error al sincronizar comandos Slash: {e}")
    check_mutes.start()
    if OWNER_ID != 1224791534436749354:
        owner = bot.get_user(OWNER_ID)
        if owner:
            await owner.send(f"🤖 **{bot.user.name}** ha iniciado correctamente. ")


@tasks.loop(minutes=1)
async def check_mutes():
    """Revisa la base de datos para desmutear usuarios cuyo tiempo ha expirado."""
    current_time = time.time()
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, guild_id FROM temp_mutes WHERE unmute_time <= %s", (current_time,))
            expired_mutes = cursor.fetchall()
            for user_id, guild_id in expired_mutes:
                guild = bot.get_guild(guild_id)
                if not guild: continue
                member = guild.get_member(user_id)
                mute_role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
                if member and mute_role and mute_role in member.roles:
                    try:
                        await member.remove_roles(mute_role, reason="Tiempo de muteo expirado.")
                        log_channel = get_log_channel(guild)
                        if log_channel:
                            await log_channel.send(embed=discord.Embed(title="🔊 Auto-Desmuteo", description=f"{member.mention} ha sido desmuteado automáticamente.", color=discord.Color.green()))
                    except discord.Forbidden:
                        print(f"Error: No se pudo desmutear al usuario {member.id} en {guild.name} (Permisos).")
            cursor.execute("DELETE FROM temp_mutes WHERE unmute_time <= %s", (current_time,))
            conn.commit()


@bot.event
async def on_member_join(member: discord.Member):
    """Asigna el auto-rol a los nuevos miembros."""
    config = get_config(member.guild)
    autorole_id = config.get('autorole_id')
    if autorole_id:
        autorole = member.guild.get_role(autorole_id)
        if autorole:
            try:
                await member.add_roles(autorole, reason="Auto-rol de bienvenida.")
            except discord.Forbidden:
                print(f"Error: No se pudo asignar el auto-rol a {member.name} (Permisos).")


@bot.event
async def on_message(message: discord.Message):
    """Maneja el sistema de XP y procesa comandos."""
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    user_id = message.author.id
    guild_id = message.guild.id
    current_time = time.time()
    _, _, last_message_time = get_level_data(user_id, guild_id)
    if current_time - last_message_time >= XP_COOLDOWN_SECONDS:
        new_level, leveled_up = update_level_data(user_id, guild_id, XP_PER_MESSAGE, current_time)
        if leveled_up:
            await message.channel.send(f"🎉 ¡Felicidades, {message.author.mention}! Has alcanzado el Nivel **{new_level}**.")
    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    """Registra los mensajes eliminados en el canal de logs."""
    if message.author.bot or not message.guild:
        return

    log_channel = get_log_channel(message.guild)
    if log_channel:
        embed = discord.Embed(
            title="🗑️ Mensaje Eliminado",
            description=f"**Autor:** {message.author.mention}\n"
                        f"**Canal:** {message.channel.mention}\n"
                        f"**Contenido:**\n{message.content}",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        await log_channel.send(embed=embed)


@bot.hybrid_command(name='play', aliases=['p'], description="Reproduce música en el canal de voz.")
async def play(ctx, *, search: str):
    if ctx.author.voice is None:
        return await ctx.send("No estás en un canal de voz.")

    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.voice_client.move_to(ctx.author.voice.channel)

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    # If the search is not a URL, perform a YouTube search.
    if not search.startswith('http'):
        search = f"ytsearch:{search}"

    player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        music_queues[ctx.guild.id].append(player)
        await ctx.send(f'**{player.title}** ha sido añadido a la cola.')
    else:
        ctx.voice_client.play(player, after=lambda x: play_next(ctx))
        await ctx.send(f"Ahora reproduciendo: **{player.title}**")

@bot.hybrid_command(name='stop', description="Detiene la música y desconecta al bot.")
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        music_queues[ctx.guild.id] = []
        await ctx.send("Reproducción detenida y el bot ha sido desconectado.")

@bot.hybrid_command(name='skip', aliases=['s'], description="Salta la canción actual.")
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Canción saltada.")


@bot.command(name="sync")
@commands.is_owner()
async def sync(ctx: commands.Context, scope: Optional[str] = 'local'):
    """
    Sincroniza los comandos de barra (slash commands) del bot.

    Este es un comando híbrido, lo que significa que puedes usarlo con `!` o `/`.
    - `!sync local` o `/sync scope:local`: Sincroniza los comandos solo para este servidor. Es la opción más rápida y recomendada para pruebas.
    - `!sync global` o `/sync scope:global`: Sincroniza los comandos para todos los servidores donde está el bot. Este proceso puede tardar hasta una hora.
    - `!sync`: Por defecto, usa el scope 'local'.
    """
    scope = scope.lower()
    if scope not in ['global', 'local']:
        await ctx.send(embed=create_error_embed(
            "Scope Inválido",
            "Por favor, elige 'local' o 'global'."
        ), ephemeral=True)
        return

    if scope == 'local':
        if ctx.guild is None:
            await ctx.send(embed=create_error_embed(
                "Error",
                "El scope 'local' solo se puede usar dentro de un servidor."
            ), ephemeral=True)
            return
        
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        message = f"Se han sincronizado **{len(synced)}** comandos para este servidor."
    else: # scope == 'global'
        synced = await bot.tree.sync()
        message = f"Se han sincronizado **{len(synced)}** comandos globalmente. Puede tardar hasta una hora en reflejarse."

    embed = create_success_embed("Sincronización Completa", message)
    await ctx.send(embed=embed, ephemeral=True)


@bot.tree.command(name='mod-ban', description='🔨 Banea a un usuario del servidor.')
@discord.app_commands.describe(member='El usuario a banear.', reason='Razón del baneo.')
@discord.app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = 'Sin razón especificada.'):
    if member.id == interaction.user.id:
        await interaction.response.send_message(embed=create_error_embed("Error", "No puedes banearte a ti mismo."), ephemeral=True)
        return
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(embed=create_error_embed("Error de Jerarquía", "No puedes banear a un usuario con un rol igual o superior."), ephemeral=True)
        return
    try:
        await member.ban(reason=f"Moderador: {interaction.user.name}, Razón: {reason}")
        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            embed = discord.Embed(title="🔨 Usuario Baneado", description=f"**Usuario:** {member.mention}\n**Moderador:** {interaction.user.mention}\n**Razón:** {reason}", color=discord.Color.dark_red(), timestamp=datetime.now())
            await log_channel.send(embed=embed)
        await interaction.response.send_message(embed=create_success_embed("Baneado", f"{member.mention} ha sido baneado. Razón: **{reason}**"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=create_error_embed("Permiso Denegado", "No tengo permisos para banear a este usuario."), ephemeral=True)

@bot.tree.command(name='mod-unban', description='🕊️ Desbanea a un usuario por su ID.')
@discord.app_commands.describe(user_id='ID del usuario a desbanear.', reason='Razón del desbaneo.')
@discord.app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str, reason: str = 'Sin razón especificada.'):
    try:
        user = await bot.fetch_user(int(user_id))
    except ValueError:
        await interaction.response.send_message(embed=create_error_embed("Error", "ID de usuario inválida."), ephemeral=True)
        return
    try:
        await interaction.guild.unban(user, reason=f"Moderador: {interaction.user.name}, Razón: {reason}")
        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            embed = discord.Embed(title="🕊️ Usuario Desbaneado", description=f"**Usuario:** {user.mention} (ID: {user.id})\n**Moderador:** {interaction.user.mention}\n**Razón:** {reason}", color=discord.Color.blue(), timestamp=datetime.now())
            await log_channel.send(embed=embed)
        await interaction.response.send_message(embed=create_success_embed("Desbaneado", f"{user.mention} ha sido desbaneado."))
    except discord.NotFound:
        await interaction.response.send_message(embed=create_error_embed("Error", "Este usuario no está baneado."), ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(embed=create_error_embed("Permiso Denegado", "No tengo permisos para desbanear a este usuario."), ephemeral=True)

@bot.tree.command(name='mod-kick', description='👟 Expulsa a un usuario del servidor.')
@discord.app_commands.describe(member='El usuario a expulsar.', reason='Razón de la expulsión.')
@discord.app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = 'Sin razón especificada.'):
    if member.id == interaction.user.id:
        await interaction.response.send_message(embed=create_error_embed("Error", "No puedes expulsarte a ti mismo."), ephemeral=True)
        return
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(embed=create_error_embed("Error de Jerarquía", "No puedes expulsar a un usuario con un rol igual o superior."), ephemeral=True)
        return
    try:
        await member.kick(reason=f"Moderador: {interaction.user.name}, Razón: {reason}")
        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            embed = discord.Embed(title="👟 Usuario Expulsado", description=f"**Usuario:** {member.mention}\n**Moderador:** {interaction.user.mention}\n**Razón:** {reason}", color=discord.Color.orange(), timestamp=datetime.now())
            await log_channel.send(embed=embed)
        await interaction.response.send_message(embed=create_success_embed("Expulsado", f"{member.mention} ha sido expulsado. Razón: **{reason}**"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=create_error_embed("Permiso Denegado", "No tengo permisos para expulsar a este usuario."), ephemeral=True)

@bot.tree.command(name='mod-mute', description='🔇 Silencia temporalmente a un usuario.')
@discord.app_commands.describe(member='El usuario a silenciar.', duration='Duración (ej: 1h, 30m, 1d).', reason='Razón del muteo.')
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = 'Sin razón especificada.'):
    try:
        if 'd' in duration:
            time_delta = timedelta(days=int(duration.replace('d', '')))
        elif 'h' in duration:
            time_delta = timedelta(hours=int(duration.replace('h', '')))
        elif 'm' in duration:
            time_delta = timedelta(minutes=int(duration.replace('m', '')))
        else:
            await interaction.response.send_message(embed=create_error_embed("Error", "Formato de duración inválido. Usa: 1d, 2h, 30m."), ephemeral=True)
            return
        mute_role = await create_role_if_not_exists(interaction.guild, MUTE_ROLE_NAME)
        if mute_role in member.roles:
            await interaction.response.send_message(embed=create_error_embed("Error", "Este usuario ya está silenciado."), ephemeral=True)
            return
        unmute_time = time.time() + time_delta.total_seconds()
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO temp_mutes (user_id, guild_id, unmute_time) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, guild_id) DO UPDATE SET unmute_time = EXCLUDED.unmute_time
                    """,
                    (member.id, interaction.guild.id, unmute_time)
                )
                conn.commit()
        await member.add_roles(mute_role, reason=f"Muteo temporal. Moderador: {interaction.user.name}. Razón: {reason}")
        log_channel = get_log_channel(interaction.guild)
        if log_channel:
            embed = discord.Embed(title="🔇 Usuario Silenciado", description=f"**Usuario:** {member.mention}\n**Moderador:** {interaction.user.mention}\n**Duración:** {duration}\n**Razón:** {reason}", color=discord.Color.dark_grey(), timestamp=datetime.now())
            await log_channel.send(embed=embed)
        await interaction.response.send_message(embed=create_success_embed("Silenciado", f"{member.mention} ha sido silenciado por **{duration}**. Razón: **{reason}**"))
    except Exception as e:
        print(f"Error al mutear: {e}")
        await interaction.response.send_message(embed=create_error_embed("Error Fatal", f"Ocurrió un error: {e}"), ephemeral=True)

@bot.tree.command(name='mod-unmute', description='🔊 Quita el silencio a un usuario.')
@discord.app_commands.describe(member='El usuario a desmutear.')
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    mute_role = discord.utils.get(interaction.guild.roles, name=MUTE_ROLE_NAME)
    if mute_role and mute_role in member.roles:
        try:
            await member.remove_roles(mute_role, reason=f"Desmuteo manual. Moderador: {interaction.user.name}")
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM temp_mutes WHERE user_id = %s AND guild_id = %s", (member.id, interaction.guild.id))
                    conn.commit()
            log_channel = get_log_channel(interaction.guild)
            if log_channel:
                embed = discord.Embed(title="🔊 Usuario Desmuteado", description=f"**Usuario:** {member.mention}\n**Moderador:** {interaction.user.mention}", color=discord.Color.orange(), timestamp=datetime.now())
                await log_channel.send(embed=embed)
            await interaction.response.send_message(embed=create_success_embed("Desmuteado", f"{member.mention} ha sido desmuteado."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=create_error_embed("Permiso Denegado", "No tengo permisos para quitar este rol."), ephemeral=True)
    else:
        await interaction.response.send_message(embed=create_error_embed("Error", "Este usuario no está silenciado."), ephemeral=True)

@bot.tree.command(name='mod-warn', description='⚠️ Aplica una advertencia a un usuario.')
@discord.app_commands.describe(member='El usuario a advertir.', reason='Razón de la advertencia.')
@discord.app_commands.checks.has_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = 'Sin razón especificada.'):
    if member.bot:
        await interaction.response.send_message(embed=create_error_embed("Error", "No puedes advertir a un bot."), ephemeral=True)
        return
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO warnings (user_id, guild_id, moderator_id, reason, timestamp) VALUES (%s, %s, %s, %s, %s)",
                (member.id, interaction.guild.id, interaction.user.id, reason, datetime.now().isoformat())
            )
            conn.commit()
    log_channel = get_log_channel(interaction.guild)
    if log_channel:
        embed = discord.Embed(title="⚠️ Nueva Advertencia", description=f"**Usuario:** {member.mention}\n**Moderador:** {interaction.user.mention}\n**Razón:** {reason}", color=discord.Color.yellow(), timestamp=datetime.now())
        await log_channel.send(embed=embed)
    await interaction.response.send_message(embed=create_success_embed("Advertencia Aplicada", f"{member.mention} ha recibido una advertencia. Razón: **{reason}**"))

@bot.tree.command(name='mod-warnings', description='📋 Muestra las advertencias de un usuario.')
@discord.app_commands.describe(member='El usuario a consultar.')
@discord.app_commands.checks.has_permissions(kick_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT moderator_id, reason, timestamp FROM warnings WHERE user_id = %s AND guild_id = %s",
                (member.id, interaction.guild.id)
            )
            results = cursor.fetchall()
    if not results:
        await interaction.response.send_message(embed=create_success_embed("Advertencias", f"{member.display_name} no tiene advertencias."), ephemeral=True)
        return
    embed = discord.Embed(title=f"📋 Advertencias de {member.display_name}", color=discord.Color.blue())
    for i, (mod_id, reason, timestamp) in enumerate(results):
        mod = interaction.guild.get_member(mod_id)
        mod_name = mod.display_name if mod else f"ID: {mod_id}"
        date_str = datetime.fromisoformat(timestamp).strftime("%d/%m/%Y %H:%M")
        embed.add_field(
            name=f"Advertencia #{i+1} ({date_str})",
            value=f"**Moderador:** {mod_name}\n**Razón:** {reason}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='mod-clearwarnings', description='🧹 Elimina todas las advertencias de un usuario.')
@discord.app_commands.describe(member='El usuario a limpiar.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM warnings WHERE user_id = %s AND guild_id = %s",
                (member.id, interaction.guild.id)
            )
            conn.commit()
    await interaction.response.send_message(embed=create_success_embed("Advertencias Eliminadas", f"Se han eliminado todas las advertencias de {member.mention}."))

@bot.tree.command(name='mod-purge', description='🗑️ Elimina una cantidad específica de mensajes en el canal actual.')
@discord.app_commands.describe(amount='Cantidad de mensajes a eliminar (máximo 100).')
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    if amount <= 0 or amount > 100:
        await interaction.response.send_message(embed=create_error_embed("Error", "Debes especificar una cantidad entre 1 y 100."), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    embed = create_success_embed("Mensajes Eliminados", f"Se han eliminado **{len(deleted)}** mensajes en {interaction.channel.mention}.")
    await interaction.followup.send(embed=embed, ephemeral=False, delete_after=5)


@bot.tree.command(name='admin-setlogs', description='⚙️ Configura el canal para los logs de moderación.')
@discord.app_commands.describe(channel='El canal de texto para enviar los logs.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO config (guild_id, log_channel_id) VALUES (%s, %s) ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id",
                (interaction.guild.id, channel.id)
            )
            conn.commit()
    await interaction.response.send_message(embed=create_success_embed("Logs Configurados", f"El canal de logs ha sido configurado a {channel.mention}."), ephemeral=True)

@bot.tree.command(name='admin-setreport', description='🚨 Configura el canal y rol para el sistema de reportes.')
@discord.app_commands.describe(channel='El canal de texto para recibir los reportes.', role='El rol a mencionar con cada reporte (opcional).')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setreport(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role = None):
    role_id = role.id if role else None
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO config (guild_id, report_channel_id, report_role_id) VALUES (%s, %s, %s) ON CONFLICT (guild_id) DO UPDATE SET report_channel_id = EXCLUDED.report_channel_id, report_role_id = EXCLUDED.report_role_id",
                (interaction.guild.id, channel.id, role_id)
            )
            conn.commit()
    role_mention = role.mention if role else "ninguno"
    await interaction.response.send_message(embed=create_success_embed("Reportes Configurados", f"El canal de reportes es {channel.mention} y el rol a mencionar es {role_mention}."), ephemeral=True)

@bot.tree.command(name='admin-setautorole', description='🤖 Asigna automáticamente un rol a los nuevos miembros.')
@discord.app_commands.describe(role='El rol a asignar automáticamente.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setautorole(interaction: discord.Interaction, role: discord.Role):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO config (guild_id, autorole_id) VALUES (%s, %s) ON CONFLICT (guild_id) DO UPDATE SET autorole_id = EXCLUDED.autorole_id",
                (interaction.guild.id, role.id)
            )
            conn.commit()
    await interaction.response.send_message(embed=create_success_embed("Auto-Rol Configurado", f"El rol de bienvenida es ahora **{role.name}**."), ephemeral=True)

@bot.tree.command(name='report', description='🗣️ Reporta a un usuario o mensaje a los moderadores.')
@discord.app_commands.describe(member='El usuario a reportar.', reason='Razón del reporte.')
async def slash_report(interaction: discord.Interaction, member: discord.Member, reason: str):
    report_channel, report_role = get_report_config(interaction.guild)
    if not report_channel:
        await interaction.response.send_message(embed=create_error_embed("Error", "El canal de reportes no ha sido configurado. Pídele a un admin que use `/admin-setreport`."), ephemeral=True)
        return
    if member.id == interaction.user.id or member.bot:
        await interaction.response.send_message(embed=create_error_embed("Error", "No puedes reportarte a ti mismo ni a un bot."), ephemeral=True)
        return
    mention_str = report_role.mention if report_role else "**¡Nuevo Reporte!**"
    embed = discord.Embed(
        title="🚨 Reporte de Usuario",
        description=f"**Reportado:** {member.mention} (ID: `{member.id}`)\n**Reportado por:** {interaction.user.mention}\n**Razón:** {reason}",
        color=discord.Color.dark_red(),
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Reporte enviado desde #{interaction.channel.name}")
    await report_channel.send(content=mention_str, embed=embed)
    await interaction.response.send_message(embed=create_success_embed("Reporte Enviado", "Tu reporte ha sido enviado a los moderadores. Gracias."), ephemeral=True)


@bot.hybrid_command(name='marry', description='Propón matrimonio a otro usuario.')
async def marry(ctx, member: discord.Member):
    user1_id = ctx.author.id
    user2_id = member.id
    guild_id = ctx.guild.id
    if user1_id == user2_id or member.bot:
        await ctx.send(embed=create_error_embed("Error", "No puedes casarte contigo mismo o con un bot."), delete_after=10)
        return
    if get_partner(user1_id, guild_id) or get_partner(user2_id, guild_id):
        await ctx.send(embed=create_error_embed("Error", "Uno de los usuarios ya está casado."), delete_after=10)
        return
    embed = discord.Embed(
        title="💍 Propuesta de Matrimonio",
        description=f"{member.mention}, **{ctx.author.display_name}** te ha propuesto matrimonio.\n\nReacciona con **✅** para aceptar o **❌** para rechazar.",
        color=discord.Color.light_grey()
    )
    message = await ctx.send(content=member.mention, embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    def check(reaction, user):
        return user.id == user2_id and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == message.id
    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
    except asyncio.TimeoutError:
        await message.edit(embed=create_error_embed("Propuesta Expirada", "La propuesta de matrimonio ha expirado."), content=None)
        await message.clear_reactions()
        return
    if str(reaction.emoji) == "✅":
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                u1, u2 = sorted([user1_id, user2_id])
                cursor.execute("INSERT INTO marriages (user1_id, user2_id, guild_id, marriage_date) VALUES (%s, %s, %s, %s)", 
                               (u1, u2, guild_id, datetime.now().isoformat()))
                conn.commit()
        await message.edit(embed=create_success_embed("¡BODAS!", f"**{ctx.author.display_name}** y **{member.display_name}** ¡se han casado! 🎉"), content=f"{ctx.author.mention} {member.mention}")
        await message.clear_reactions()
    else:
        await message.edit(embed=create_error_embed("Propuesta Rechazada", f"**{member.display_name}** ha rechazado la propuesta de matrimonio."), content=None)
        await message.clear_reactions()

@bot.hybrid_command(name='divorce', description="Inicia el proceso de divorcio.")
async def divorce(ctx):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    partner_id = get_partner(user_id, guild_id)
    if not partner_id:
        await ctx.send(embed=create_error_embed("Error", "No estás casado con nadie."), delete_after=10)
        return
    partner = ctx.guild.get_member(partner_id)
    partner_name = partner.display_name if partner else f"Usuario con ID {partner_id}"
    embed = discord.Embed(
        title="💔 Solicitud de Divorcio",
        description=f"¿Estás seguro de que quieres divorciarte de **{partner_name}**?\n\nReacciona con **💔** para confirmar el divorcio.",
        color=discord.Color.red()
    )
    message = await ctx.send(embed=embed)
    await message.add_reaction("💔")
    def check(reaction, user):
        return user.id == user_id and str(reaction.emoji) == "💔" and reaction.message.id == message.id
    try:
        await bot.wait_for('reaction_add', timeout=20.0, check=check)
    except asyncio.TimeoutError:
        await message.edit(embed=create_error_embed("Confirmación Expirada", "La solicitud de divorcio ha expirado."), content=None)
        await message.clear_reactions()
        return
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            u1, u2 = sorted([user_id, partner_id])
            cursor.execute("DELETE FROM marriages WHERE user1_id = %s AND user2_id = %s AND guild_id = %s", (u1, u2, guild_id))
            conn.commit()
    await message.edit(embed=create_success_embed("Divorcio Consumado", f"**{ctx.author.display_name}** se ha divorciado de **{partner_name}**. ¡Libertad!"), content=None)
    await message.clear_reactions()

@bot.hybrid_command(name='spouse', aliases=['wife', 'husband'], description="Muestra con quién estás casado.")
async def spouse(ctx):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    data = get_marriage_data(user_id, guild_id)
    if not data:
        await ctx.send(embed=create_error_embed("Matrimonio", "No estás casado con nadie."), delete_after=10)
        return
    _, _, date_str = data
    partner_id = data[1] if data[0] == user_id else data[0]
    partner = ctx.guild.get_member(partner_id)
    partner_name = partner.display_name if partner else f"ID: {partner_id}"
    marriage_date = datetime.fromisoformat(date_str).strftime("%d de %B de %Y")
    embed = discord.Embed(
        title=f"❤️ Matrimonio de {ctx.author.display_name}",
        description=f"Estás casado(a) con **{partner_name}**.",
        color=discord.Color.red()
    )
    embed.add_field(name="Fecha de Aniversario", value=marriage_date, inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="invite", description="¡Miau! Consigue el enlace para invitar a este michi a tu servidor.")
async def invite(ctx: commands.Context):
    """Genera un enlace para invitar al bot a un servidor."""
    invite_url = generate_invite_link(CLIENT_ID_INVITE, PERMISSION_CODE_INVITE)
    embed = discord.Embed(
        title="🎀 ¡Invítame a tu Servidor!",
        description=f"¡Hola! Soy **{ctx.bot.user.name}**. Haz clic en el enlace de abajo para añadirme a tu servidor y disfrutar de mis funciones.\n\n"
                    f"**[Haz clic aquí para invitar]({invite_url})**",
        color=discord.Color.from_rgb(173, 216, 230)
    )
    embed.set_thumbnail(url=ctx.bot.user.display_avatar.url)
    embed.set_footer(text="¡Gracias por tu apoyo! 💖")
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(name='balance', aliases=['bal'], description="Muestra tu saldo o el de otro usuario.")
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = get_balance(member.id, ctx.guild.id)
    embed = discord.Embed(
        title="🏦 Saldo Bancario",
        description=f"El saldo de **{member.display_name}** es de **{balance} 💰**.",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(name='daily', description="Reclama tu recompensa diaria.")
async def daily(ctx):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    last_daily = get_last_action_time(user_id, guild_id, 'daily')
    cooldown = ECONOMY_COOLDOWN_DAILY_HOURS * 3600
    current_time = time.time()
    if current_time - last_daily < cooldown:
        remaining_seconds = cooldown - (current_time - last_daily)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        await ctx.send(embed=create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para reclamar tu próxima recompensa diaria."), delete_after=10)
        return
    update_balance(user_id, guild_id, DAILY_REWARD)
    set_last_action_time(user_id, guild_id, 'daily')
    await ctx.send(embed=create_success_embed("Recompensa Diaria", f"Has reclamado tu recompensa diaria de **{DAILY_REWARD} 💰**."))


@bot.hybrid_command(name='work', description="Trabaja para ganar dinero.")
async def work(ctx):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    last_work = get_last_action_time(user_id, guild_id, 'work')
    cooldown = ECONOMY_COOLDOWN_WORK_HOURS * 3600
    current_time = time.time()
    if current_time - last_work < cooldown:
        remaining_seconds = cooldown - (current_time - last_work)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        await ctx.send(embed=create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para volver a trabajar."), delete_after=10)
        return
    earnings = random.randint(100, 300)
    jobs = ["programar código", "servir café", "pasear perros", "reparar computadoras", "diseñar logos"]
    job = random.choice(jobs)
    update_balance(user_id, guild_id, earnings)
    set_last_action_time(user_id, guild_id, 'work')
    await ctx.send(embed=create_success_embed("Trabajo Realizado", f"Fuiste a **{job}** y ganaste **{earnings} 💰**."))


@bot.hybrid_command(name='flip', description="Apuesta a cara o cruz.")
async def flip(ctx, side: str, amount: int):
    side = side.lower()
    if side not in ['cara', 'cruz']:
        await ctx.send(embed=create_error_embed("Error", "Elige 'cara' o 'cruz'."), delete_after=10)
        return
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Error", "La cantidad debe ser positiva."), delete_after=10)
        return
    user_balance = get_balance(ctx.author.id, ctx.guild.id)
    if user_balance < amount:
        await ctx.send(embed=create_error_embed("Error", "No tienes suficiente dinero."), delete_after=10)
        return
    result = random.choice(['cara', 'cruz'])
    if result == side:
        update_balance(ctx.author.id, ctx.guild.id, amount)
        new_balance = user_balance + amount
        await ctx.send(embed=create_success_embed("¡Ganaste!", f"Salió **{result}**. ¡Ganaste **{amount} 💰**! Saldo: {new_balance} 💰"))
    else:
        update_balance(ctx.author.id, ctx.guild.id, -amount)
        new_balance = user_balance - amount
        await ctx.send(embed=create_error_embed("Perdiste", f"Salió **{result}**. Perdiste **{amount} 💰**. Saldo: {new_balance} 💰"))


@bot.hybrid_command(name='slots', description="Juega a las tragaperras.")
async def slots(ctx, amount: int):
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Error", "La cantidad debe ser positiva."), delete_after=10)
        return
    user_balance = get_balance(ctx.author.id, ctx.guild.id)
    if user_balance < amount:
        await ctx.send(embed=create_error_embed("Error", "No tienes suficiente dinero."), delete_after=10)
        return
    emojis = ["🍒", "🍇", "🍋", "7️⃣"]
    results = [random.choice(emojis) for _ in range(3)]
    slot_display = f"| **{' | '.join(results)}** |"
    if results[0] == results[1] == results[2]:
        winnings = amount * 7
        update_balance(ctx.author.id, ctx.guild.id, winnings)
        embed = create_success_embed("¡JACKPOT! 🎰🎰🎰", f"{slot_display}\n¡Ganaste **{winnings} 💰**! (x7)")
    elif results[0] == results[1] or results[1] == results[2]:
        winnings = amount * 2
        update_balance(ctx.author.id, ctx.guild.id, winnings)
        embed = create_success_embed("¡Doble! 🎰🎰", f"{slot_display}\n¡Ganaste **{winnings} 💰**! (x2)")
    else:
        update_balance(ctx.author.id, ctx.guild.id, -amount)
        embed = create_error_embed("Perdiste 💸", f"{slot_display}\nPerdiste **{amount} 💰**.")
    new_balance = get_balance(ctx.author.id, ctx.guild.id)
    embed.set_footer(text=f"Saldo actual: {new_balance} 💰")
    await ctx.send(embed=embed)


@bot.hybrid_command(name='rob', description="Intenta robar dinero a otro usuario.")
async def rob(ctx, member: discord.Member):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    target_id = member.id
    if user_id == target_id or member.bot:
        await ctx.send(embed=create_error_embed("Error", "No puedes robarte a ti mismo o a un bot."), delete_after=10)
        return
    last_rob = get_last_action_time(user_id, guild_id, 'rob')
    cooldown = 2 * 3600
    current_time = time.time()
    if current_time - last_rob < cooldown:
        remaining_seconds = cooldown - (current_time - last_rob)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        await ctx.send(embed=create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para volver a robar."), delete_after=10)
        return
    target_balance = get_balance(target_id, guild_id)
    if target_balance < 1000:
        await ctx.send(embed=create_error_embed("Pobreza", f"{member.display_name} es demasiado pobre para ser robado (necesita al menos 1000 💰)."), delete_after=10)
        return
    set_last_action_time(user_id, guild_id, 'rob')
    if random.random() < 0.4:
        rob_amount = int(target_balance * random.uniform(0.1, 0.3))
        update_balance(user_id, guild_id, rob_amount)
        update_balance(target_id, guild_id, -rob_amount)
        await ctx.send(embed=create_success_embed("¡Robo Exitoso! 😈", f"Le robaste **{rob_amount} 💰** a {member.display_name}. ¡Huye!"))
    else:
        fine = random.randint(100, 500)
        update_balance(user_id, guild_id, -fine)
        await ctx.send(embed=create_error_embed("¡Atrapado! 🚨", f"Fuiste atrapado intentando robar a {member.display_name}. Tuviste que pagar una multa de **{fine} 💰**."))




def get_leaderboard_data(guild_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, xp, level FROM leveling WHERE guild_id = %s ORDER BY level DESC, xp DESC LIMIT 10",
                (guild_id,)
            )
            return cursor.fetchall()

@bot.hybrid_command(name='rank', description="Muestra tu nivel y XP o el de otro usuario.")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp, level, _ = get_level_data(member.id, ctx.guild.id)
    xp_needed_next = get_xp_needed(level)
    embed = discord.Embed(
        title=f"📊 Rango de {member.display_name}",
        description=f"**Nivel Actual:** {level}\n**XP:** {xp}/{xp_needed_next}\n**Progreso:** {'█' * int(xp * 10 / xp_needed_next)}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='leaderboard', aliases=['top'], description="Muestra la tabla de clasificación de niveles.")
async def leaderboard(ctx):
    top_users = get_leaderboard_data(ctx.guild.id)
    if not top_users:
        await ctx.send(embed=create_error_embed("Error", "No hay datos de nivelación para mostrar."), delete_after=10)
        return
    description = []
    for i, (user_id, xp, level) in enumerate(top_users):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"ID: {user_id}"
        rank_emoji = ""
        if i == 0: rank_emoji = "🥇"
        elif i == 1: rank_emoji = "🥈"
        elif i == 2: rank_emoji = "🥉"
        else: rank_emoji = f"#{i+1}"
        description.append(f"{rank_emoji} **{name}** - Nivel **{level}** (XP: {xp})")
    embed = discord.Embed(
        title="🏆 Tabla de Clasificación (Niveles)",
        description='\n'.join(description),
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)


@bot.tree.command(name='admin-setmoney', description='💰 Establece el saldo de un usuario.')
@discord.app_commands.describe(member='El usuario al que quieres modificar el saldo.', amount='La nueva cantidad de saldo.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount < 0:
        await interaction.response.send_message(embed=create_error_embed("Error", "La cantidad debe ser 0 o positiva."), ephemeral=True)
        return
    new_balance = set_balance(member.id, interaction.guild.id, amount)
    await interaction.response.send_message(embed=create_success_embed("Saldo Actualizado", f"El saldo de **{member.display_name}** ha sido establecido a **{new_balance} 💰**."), ephemeral=True)


@bot.tree.command(name='admin-addshoprole', description='🛒 Pone un rol a la venta en la tienda de economía.')
@discord.app_commands.describe(role='El rol que quieres vender.', price='El precio del rol.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_addshoprole(interaction: discord.Interaction, role: discord.Role, price: int):
    if price <= 0:
        await interaction.response.send_message(embed=create_error_embed("Error", "El precio debe ser positivo."), ephemeral=True)
        return
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO role_shop (guild_id, role_id, price) VALUES (%s, %s, %s) ON CONFLICT (guild_id, role_id) DO UPDATE SET price = EXCLUDED.price",
                (interaction.guild.id, role.id, price)
            )
            conn.commit()
    await interaction.response.send_message(embed=create_success_embed("Rol Añadido a la Tienda", f"El rol **{role.name}** está ahora a la venta por **{price} 💰**."), ephemeral=True)


@bot.hybrid_command(name='shop', description="Muestra la tienda de roles.")
async def shop(ctx):
    shop_roles = get_shop_roles(ctx.guild.id)
    if not shop_roles:
        await ctx.send(embed=create_error_embed("Tienda Vacía", "No hay roles a la venta en este momento."), delete_after=10)
        return
    description = []
    for role_id, price in shop_roles:
        role = ctx.guild.get_role(role_id)
        if role:
            description.append(f"**{role.name}** ➡️ **{price} 💰**")
    embed = discord.Embed(
        title="🛍️ Tienda de Roles",
        description='\n'.join(description) + "\n\nUsa `!buyrole <NombreDelRol>` para comprar.",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(name='buyrole', description="Compra un rol de la tienda.")
async def buyrole(ctx, *, role_name: str):
    role_name = role_name.strip()
    shop_roles = get_shop_roles(ctx.guild.id)
    role_to_buy = None
    price = 0
    for role_id, p in shop_roles:
        role = ctx.guild.get_role(role_id)
        if role and role.name.lower() == role_name.lower():
            role_to_buy = role
            price = p
            break
    if not role_to_buy:
        await ctx.send(embed=create_error_embed("Error", f"El rol **{role_name}** no está a la venta."), delete_after=10)
        return
    if role_to_buy in ctx.author.roles:
        await ctx.send(embed=create_error_embed("Error", f"Ya tienes el rol **{role_to_buy.name}**."), delete_after=10)
        return
    user_balance = get_balance(ctx.author.id, ctx.guild.id)
    if user_balance < price:
        await ctx.send(embed=create_error_embed("Error", f"No tienes suficiente dinero. Necesitas **{price} 💰**."), delete_after=10)
        return
    try:
        update_balance(ctx.author.id, ctx.guild.id, -price)
        await ctx.author.add_roles(role_to_buy, reason="Compra de rol en la tienda.")
        new_balance = get_balance(ctx.author.id, ctx.guild.id)
        await ctx.send(embed=create_success_embed("Compra Exitosa", f"Has comprado el rol **{role_to_buy.name}** por **{price} 💰**. Saldo restante: {new_balance} 💰"))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("Error de Permisos", "No puedo darte ese rol (puede que el rol esté por encima del mío)."), delete_after=10)


app = Flask(__name__)

@app.route('/')
def home():
    return "El bot está vivo."

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("¡ERROR CRÍTICO! La variable DISCORD_TOKEN no está configurada. El bot no puede iniciarse.")
        return
    if not DATABASE_URL:
        print("¡ERROR CRÍTICO! La variable DATABASE_URL no está configurada. El bot no puede conectarse a la base de datos.")
        return
    print("Conectando el bot a Discord...")
    try:
        bot.run(TOKEN)
    except discord.HTTPException as e:
        print(f"ERROR: Token inválido o problema de conexión. Revisa tu Token. Error: {e.status} {e.text}")
    except Exception as e:
        print(f"Ocurrió un error inesperado al iniciar el bot: {e}")


if __name__ == '__main__':
    from threading import Thread
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    run_bot()
