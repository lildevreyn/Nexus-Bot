import discord
from discord.ext import commands, tasks
import sqlite3
import random
import time
from datetime import datetime, timedelta
from contextlib import closing
import asyncio
from typing import Optional
import os

# --- NUEVO: IMPORTACIONES PARA MANTENER EL BOT VIVO EN RENDER ---
from flask import Flask
from threading import Thread

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
            description=(
                f"¡Hola! La mayoría de los comandos funcionan con `{prefix}` (ej: `{prefix}ping`) y con `/` (ej: `/ping`).\n"
                "**Excepciones:** `!sync` solo funciona con prefijo `!`."
            ),
            color=discord.Color.blurple()
        )

        mod_cmds = (
            "`/mod-ban <user> <razón>`: Banea a un usuario.",
            "`/mod-kick <user> <razón>`: Expulsa a un usuario.",
            "`/mod-mute <user> <duración>`: Silencia temporalmente.",
            "`/mod-warn <user> <razón>`: Aplica una advertencia.",
            "`/mod-purge <cantidad>`: Borra mensajes del canal."
        )
        embed.add_field(name="🛡️ Moderación (Solo /)", value='\n'.join(mod_cmds), inline=False)

        eco_cmds = (
            "`!balance` / `/balance` (`bal`): Muestra tu saldo.",
            "`!daily` / `/daily`: Reclama tu recompensa diaria.",
            "`!work` / `/work`: Gana dinero por trabajar.",
            "`!flip` / `/flip`: Apuesta a cara o cruz.",
            "`!rank` / `/rank`: Muestra tu nivel y XP.",
            "`!leaderboard` / `/leaderboard` (`top`): Muestra la tabla de niveles.",
            "`!shop` / `/shop`: Ve los roles a la venta.",
            "`!buyrole <rol>` / `/buyrole <rol>`: Compra un rol de la tienda."
        )
        embed.add_field(name="💰 Economía y Niveles", value='\n'.join(eco_cmds), inline=False)

        marriage_cmds = (
            "`!marry` / `/marry <user>`: Propone matrimonio.",
            "`!divorce` / `/divorce`: Inicia el proceso de divorcio.",
            "`!spouse` / `/spouse`: Muestra con quién estás casado."
        )
        embed.add_field(name="❤️ Bodas", value='\n'.join(marriage_cmds), inline=False)

        music_cmds = (
            "`!play <canción>` (`p`): Reproduce música.",
            "`!stop`: Detiene la reproducción y desconecta.",
            "`!skip` (`s`): Salta la canción actual."
        )
        embed.add_field(name="🎶 Música (Solo !)", value='\n'.join(music_cmds), inline=False)

        util_cmds = (
            "`/report <user> <razón>`: Reporta a un usuario.",
            "`!invite` / `/invite`: Genera un enlace de invitación para el bot.",
            "`/admin-setlogs <canal>`: Configura el canal de logs.",
            "`/admin-panelrol`: Muestra el panel de auto-roles.",
            "`/admin-settickets <rol> <categoría>`: Configura el sistema de tickets.",
            "`/admin-ticketpanel`: Muestra el panel de tickets.",
            "`/admin-setmoney <user> <monto>`: Establece el saldo (Admin).",
            "`!sync` (Solo !): Sincroniza comandos (Owner)."
        )
        embed.add_field(name="🛠️ Utilidad / Admin", value='\n'.join(util_cmds), inline=False)

        embed.set_footer(text=f"Prefijo: {prefix} | Desarrollado por ReynDev.")
        await ctx.send(embed=embed)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Soporte General", style=discord.ButtonStyle.primary, custom_id="ticket_support"))
        self.add_item(discord.ui.Button(label="Reportar un Usuario", style=discord.ButtonStyle.danger, custom_id="ticket_report"))
        self.add_item(discord.ui.Button(label="Otros", style=discord.ButtonStyle.secondary, custom_id="ticket_other"))

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Cerrar Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close"))

# ----------------------------------------------------
# 2. CONFIGURACIÓN Y CONSTANTES
# ----------------------------------------------------


OWNER_ID = 1224791534436749354


PREFIX = '!'
DB_NAME = 'bot_data.db'
INTENTS = discord.Intents.all()
MUTE_ROLE_NAME = "Silenciado"


DAILY_REWARD = 500
ECONOMY_COOLDOWN_DAILY_HOURS = 24
ECONOMY_COOLDOWN_WORK_HOURS = 1


XP_PER_MESSAGE = 15
XP_COOLDOWN_SECONDS = 60


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

def initialize_db():
    """Inicializa la base de datos y crea las tablas necesarias."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER,
                report_channel_id INTEGER,
                report_role_id INTEGER,
                autorole_id INTEGER
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS temp_mutes (
                user_id INTEGER,
                guild_id INTEGER,
                unmute_time REAL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                guild_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                user_id INTEGER,
                guild_id INTEGER,
                balance INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id INTEGER,
                guild_id INTEGER,
                action TEXT,
                last_time REAL,
                PRIMARY KEY (user_id, guild_id, action)
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leveling (
                user_id INTEGER,
                guild_id INTEGER,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_message_time REAL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS role_shop (
                guild_id INTEGER,
                role_id INTEGER,
                price INTEGER,
                PRIMARY KEY (guild_id, role_id)
            )
        """)


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user1_id INTEGER,
                user2_id INTEGER,
                guild_id INTEGER,
                marriage_date TEXT,
                PRIMARY KEY (user1_id, user2_id, guild_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_configs (
                guild_id INTEGER PRIMARY KEY,
                support_role_id INTEGER,
                ticket_category_id INTEGER
            )
        """)

        conn.commit()

# --- Funciones de Configuración ---

def get_config(guild: discord.Guild):
    """Obtiene la configuración del servidor."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT log_channel_id, report_channel_id, report_role_id, autorole_id FROM config WHERE guild_id = ?", (guild.id,))
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

def get_ticket_config(guild_id: int) -> dict:
    """Obtiene la configuración de tickets del servidor."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT support_role_id, ticket_category_id FROM ticket_configs WHERE guild_id = ?", (guild_id,))
        result = cursor.fetchone()
        if result:
            return {'support_role_id': result[0], 'ticket_category_id': result[1]}
        return {}

# --- Funciones de Economía ---

def get_balance(user_id: int, guild_id: int) -> int:
    """Obtiene el saldo de un usuario."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO economy (user_id, guild_id) VALUES (?, ?)", (user_id, guild_id))
        conn.commit()
        cursor.execute("SELECT balance FROM economy WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        result = cursor.fetchone()
        return result[0] if result else 0

def update_balance(user_id: int, guild_id: int, amount: int):
    """Añade o resta una cantidad al saldo de un usuario."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO economy (user_id, guild_id) VALUES (?, ?)", (user_id, guild_id))
        cursor.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ? AND guild_id = ?", (amount, user_id, guild_id))
        conn.commit()

def set_balance(user_id: int, guild_id: int, amount: int) -> int:
    """Establece el saldo de un usuario a una cantidad específica."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO economy (user_id, guild_id) VALUES (?, ?)", (user_id, guild_id))
        cursor.execute("UPDATE economy SET balance = ? WHERE user_id = ? AND guild_id = ?", (amount, user_id, guild_id))
        conn.commit()
        return get_balance(user_id, guild_id)


# --- Funciones de Cooldowns ---

def get_last_action_time(user_id: int, guild_id: int, action: str) -> float:
    """Obtiene la última hora de una acción específica (timestamp)."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_time FROM cooldowns WHERE user_id = ? AND guild_id = ? AND action = ?", (user_id, guild_id, action))
        result = cursor.fetchone()
        return result[0] if result else 0.0

def set_last_action_time(user_id: int, guild_id: int, action: str):
    """Establece la última hora de una acción específica al tiempo actual."""
    current_time = time.time()
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO cooldowns (user_id, guild_id, action, last_time) VALUES (?, ?, ?, ?)",
            (user_id, guild_id, action, current_time)
        )
        conn.commit()


# --- Funciones de Nivelación (XP) ---

def get_level_data(user_id: int, guild_id: int) -> tuple[int, int, float]:
    """Obtiene XP, Nivel y el último tiempo de mensaje de un usuario."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO leveling (user_id, guild_id) VALUES (?, ?)", (user_id, guild_id))
        conn.commit()
        cursor.execute("SELECT xp, level, last_message_time FROM leveling WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        result = cursor.fetchone()
        return result if result else (0, 0, 0.0)

def get_xp_needed(level: int) -> int:
    """Calcula el XP necesario para el siguiente nivel."""
    return 100 + level * 50

def update_level_data(user_id: int, guild_id: int, xp_to_add: int, last_message_time: float) -> tuple[int, bool]:
    """Actualiza los datos de XP y Nivel de un usuario. Retorna el nuevo nivel y si subió de nivel."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
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
            "UPDATE leveling SET xp = ?, level = ?, last_message_time = ? WHERE user_id = ? AND guild_id = ?",
            (new_xp, new_level, last_message_time, user_id, guild_id)
        )
        conn.commit()
        return new_level, leveled_up

# --- Funciones de Tienda ---

def get_shop_roles(guild_id: int) -> list[tuple[int, int]]:
    """Obtiene todos los roles a la venta en el servidor."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role_id, price FROM role_shop WHERE guild_id = ?", (guild_id,))
        return cursor.fetchall()

# --- Funciones de Bodas ---

def get_partner(user_id: int, guild_id: int) -> Optional[int]:
    """Obtiene la ID del compañero de matrimonio."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user2_id, user1_id FROM marriages WHERE (user1_id = ? OR user2_id = ?) AND guild_id = ?", (user_id, user_id, guild_id))
        result = cursor.fetchone()

        if result:
            return result[0] if result[0] != user_id else result[1]
        return None

def get_marriage_data(user_id: int, guild_id: int) -> Optional[tuple[int, int, str]]:
    """Obtiene (user1_id, user2_id, marriage_date) de un matrimonio."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user1_id, user2_id, marriage_date FROM marriages WHERE (user1_id = ? OR user2_id = ?) AND guild_id = ?", (user_id, user_id, guild_id))
        return cursor.fetchone()

# ----------------------------------------------------
# [NUEVA SECCIÓN] 4.5. FUNCIONES DE UTILIDAD DE INVITACIÓN
# ----------------------------------------------------

CLIENT_ID_INVITE = "1443693871061008457"  
PERMISSION_CODE_INVITE = 2424032374               

def generate_invite_link(client_id: str, permissions: int) -> str:
    """Genera el enlace de invitación para el bot con los permisos especificados."""
    return f"https://discord.com/oauth2/authorize?client_id={client_id}&scope=bot%20applications.commands&permissions={permissions}"


# ----------------------------------------------------
# 5. EVENTOS Y TAREAS PERIÓDICAS
# ----------------------------------------------------

@bot.event
async def on_ready():
    """Se ejecuta cuando el bot está listo y conectado a Discord."""
    print(f'Bot conectado como {bot.user.name} (ID: {bot.user.id})')
    initialize_db()

    
    try:
        await bot.tree.sync()
        print("Comandos Slash sincronizados exitosamente.")
    except Exception as e:
        print(f"Error al sincronizar comandos Slash: {e}")

    
    check_mutes.start()

    
    if OWNER_ID != 1224791534436749354:
        owner = bot.get_user(OWNER_ID)
        if owner:
            await owner.send(f"🤖 **{bot.user.name}** ha iniciado correctamente. ")

    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())

async def _create_ticket_channel(interaction: discord.Interaction, ticket_type: str):
    """Crea un nuevo canal de ticket."""
    guild = interaction.guild
    user = interaction.user
    config = get_ticket_config(guild.id)

    support_role_id = config.get('support_role_id')
    category_id = config.get('ticket_category_id')

    if not support_role_id or not category_id:
        await interaction.response.send_message("El sistema de tickets no está configurado.", ephemeral=True)
        return

    support_role = guild.get_role(support_role_id)
    category = guild.get_channel(category_id)

    if not support_role or not category:
        await interaction.response.send_message("Error de configuración de tickets. Contacta a un admin.", ephemeral=True)
        return

    # Evitar crear múltiples tickets
    ticket_channel_name = f"ticket-{user.name}-{ticket_type.lower()}"
    existing_channel = discord.utils.get(guild.text_channels, name=ticket_channel_name)
    if existing_channel:
        await interaction.response.send_message(f"Ya tienes un ticket abierto en {existing_channel.mention}.", ephemeral=True)
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        support_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    try:
        channel = await guild.create_text_channel(
            name=ticket_channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Ticket de {user.name}. Tipo: {ticket_type}"
        )
    except discord.Forbidden:
        await interaction.response.send_message("No tengo permisos para crear canales.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Ticket de {ticket_type}",
        description=f"Hola {user.mention}, ¡gracias por contactarnos! Un miembro de {support_role.mention} te atenderá pronto. "
                    f"Por favor, describe tu problema con el mayor detalle posible.",
        color=discord.Color.green()
    )
    await channel.send(embed=embed, view=TicketCloseView())
    await interaction.response.send_message(f"Ticket creado en {channel.mention}", ephemeral=True)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Maneja las interacciones de los botones de tickets."""
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data['custom_id']

        if custom_id.startswith("ticket_"):
            if custom_id == "ticket_close":

                config = get_ticket_config(interaction.guild.id)
                support_role_id = config.get('support_role_id')

                if not support_role_id:
                    await interaction.response.send_message("El rol de soporte no está configurado.", ephemeral=True)
                    return

                support_role = interaction.guild.get_role(support_role_id)


                if support_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message("No tienes permiso para cerrar este ticket.", ephemeral=True)
                    return

                await interaction.response.send_message("El ticket se cerrará en 5 segundos...")
                await asyncio.sleep(5)
                await interaction.channel.delete(reason="Ticket cerrado por un moderador.")

            else:
                ticket_type = "Desconocido"
                if custom_id == "ticket_support":
                    ticket_type = "Soporte"
                elif custom_id == "ticket_report":
                    ticket_type = "Reporte"
                elif custom_id == "ticket_other":
                    ticket_type = "Otro"

                await _create_ticket_channel(interaction, ticket_type)


@tasks.loop(minutes=1)
async def check_mutes():
    """Revisa la base de datos para desmutear usuarios cuyo tiempo ha expirado."""
    current_time = time.time()

    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, guild_id FROM temp_mutes WHERE unmute_time <= ?", (current_time,))
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

        # Eliminar registro de la DB
        with closing(sqlite3.connect(DB_NAME)) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM temp_mutes WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
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

    
    xp, level, last_message_time = get_level_data(user_id, guild_id)

    if current_time - last_message_time >= XP_COOLDOWN_SECONDS:
        new_level, leveled_up = update_level_data(user_id, guild_id, XP_PER_MESSAGE, current_time)

        if leveled_up:
            await message.channel.send(f"🎉 ¡Felicidades, {message.author.mention}! Has alcanzado el Nivel **{new_level}**.")


    await bot.process_commands(message)


# ----------------------------------------------------
# 6. COMANDOS DE MÚSICA (PREFIX)
# ----------------------------------------------------


@bot.command(name='play', aliases=['p'])
async def play(ctx, *, search: str):
    await ctx.send(embed=create_error_embed("Música", "Este es un placeholder. Lógica: Reproducir o añadir a la cola."))

@bot.command(name='stop')
async def stop(ctx):
    await ctx.send(embed=create_error_embed("Música", "Este es un placeholder. Lógica: Detener y desconectar."))

@bot.command(name='skip', aliases=['s'])
async def skip(ctx):
    await ctx.send(embed=create_error_embed("Música", "Este es un placeholder. Lógica: Saltar la canción actual."))


# ----------------------------------------------------
# 7. COMANDOS DE MODERACION (SLASH COMMANDS) - Prefijo: mod-
# ----------------------------------------------------

@bot.command(name='sync')
async def sync_commands(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send(embed=create_error_embed("Permiso Denegado", "Solo el propietario del bot puede usar este comando."), ephemeral=True)
        return

    try:
        synced = await bot.tree.sync()
        embed = create_success_embed(
            "Sincronización Manual Completa ✅",
            f"Se han sincronizado **{len(synced)}** comandos. Por favor, reinicia Discord (CTRL+R) para verlos."
        )
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Error de Sincronización", f"No se pudieron sincronizar los comandos. Razón: `{e}`"))


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

        with closing(sqlite3.connect(DB_NAME)) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO temp_mutes (user_id, guild_id, unmute_time) VALUES (?, ?, ?)", (member.id, interaction.guild.id, unmute_time))
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
            with closing(sqlite3.connect(DB_NAME)) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM temp_mutes WHERE user_id = ?", (member.id,))
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

    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO warnings (user_id, guild_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
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
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT moderator_id, reason, timestamp FROM warnings WHERE user_id = ? AND guild_id = ?",
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
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM warnings WHERE user_id = ? AND guild_id = ?",
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


# ----------------------------------------------------
# 8. COMANDOS DE CONFIGURACIÓN (SLASH COMMANDS) - Prefijo: admin-
# ----------------------------------------------------

@bot.tree.command(name='admin-setlogs', description='⚙️ Configura el canal para los logs de moderación.')
@discord.app_commands.describe(channel='El canal de texto para enviar los logs.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (guild_id, log_channel_id) VALUES (?, ?)", (interaction.guild.id, channel.id))
        conn.commit()

    await interaction.response.send_message(embed=create_success_embed("Logs Configurados", f"El canal de logs ha sido configurado a {channel.mention}."), ephemeral=True)

@bot.tree.command(name='admin-setreport', description='🚨 Configura el canal y rol para el sistema de reportes.')
@discord.app_commands.describe(channel='El canal de texto para recibir los reportes.', role='El rol a mencionar con cada reporte (opcional).')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setreport(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role = None):
    role_id = role.id if role else None
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (guild_id, report_channel_id, report_role_id) VALUES (?, ?, ?)", (interaction.guild.id, channel.id, role_id))
        conn.commit()

    role_mention = role.mention if role else "ninguno"
    await interaction.response.send_message(embed=create_success_embed("Reportes Configurados", f"El canal de reportes es {channel.mention} y el rol a mencionar es {role_mention}."), ephemeral=True)

@bot.tree.command(name='admin-setautorole', description='🤖 Asigna automáticamente un rol a los nuevos miembros.')
@discord.app_commands.describe(role='El rol a asignar automáticamente.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setautorole(interaction: discord.Interaction, role: discord.Role):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (guild_id, autorole_id) VALUES (?, ?)", (interaction.guild.id, role.id))
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

@bot.tree.command(name='admin-settickets', description='⚙️ Configura el sistema de tickets.')
@discord.app_commands.describe(support_role='El rol que atenderá los tickets.', category='La categoría donde se crearán los tickets.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_settickets(interaction: discord.Interaction, support_role: discord.Role, category: discord.CategoryChannel):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO ticket_configs (guild_id, support_role_id, ticket_category_id) VALUES (?, ?, ?)",
                       (interaction.guild.id, support_role.id, category.id))
        conn.commit()

    await interaction.response.send_message(embed=create_success_embed("Configuración de Tickets Guardada",
        f"Se ha establecido que el rol {support_role.mention} atenderá los tickets en la categoría **{category.name}**."), ephemeral=True)

@bot.tree.command(name='admin-ticketpanel', description='📢 Envía el panel para crear tickets a este canal.')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_ticketpanel(interaction: discord.Interaction):
    config = get_ticket_config(interaction.guild.id)
    if not config.get('support_role_id') or not config.get('ticket_category_id'):
        await interaction.response.send_message(embed=create_error_embed("Configuración Incompleta",
            "El sistema de tickets no está configurado. Usa `/admin-settickets` para establecer el rol de soporte y la categoría."), ephemeral=True)
        return

    embed = discord.Embed(
        title="🎟️ Centro de Soporte",
        description="¿Necesitas ayuda o quieres reportar a alguien? Haz clic en uno de los botones de abajo para crear un ticket y nuestro equipo te atenderá.",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Los tickets son privados entre tú y el equipo de soporte.")

    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ Panel de tickets enviado.", ephemeral=True, delete_after=5)


# ----------------------------------------------------
# 9. COMANDOS DE BODAS (PREFIX Y SLASH)
# ----------------------------------------------------

@bot.command(name='marry', description='💍 Propón matrimonio a otro usuario.')
async def marry(ctx, member: discord.Member):
    await _propose_marriage(ctx, member)

@bot.command(name='divorce')
async def divorce(ctx):
    await _initiate_divorce(ctx)

@bot.command(name='spouse', aliases=['wife', 'husband'])
async def spouse(ctx):
    await _show_spouse(ctx)

@bot.tree.command(name='marry', description='💍 Propón matrimonio a otro usuario.')
@discord.app_commands.describe(member='El usuario al que quieres proponer matrimonio.')
async def slash_marry(interaction: discord.Interaction, member: discord.Member):
    await _propose_marriage(interaction, member)

@bot.tree.command(name='divorce', description='💔 Inicia el proceso de divorcio.')
async def slash_divorce(interaction: discord.Interaction):
    await _initiate_divorce(interaction)

@bot.tree.command(name='spouse', description='💑 Muestra con quién estás casado.')
async def slash_spouse(interaction: discord.Interaction):
    await _show_spouse(interaction)

# ----------------------------------------------------
# 9.5. HELPER FUNCTIONS FOR COMMANDS
# ----------------------------------------------------

# --- Economy Helpers ---

def _balance_embed(member: discord.Member) -> discord.Embed:
    balance = get_balance(member.id, member.guild.id)
    embed = discord.Embed(
        title="🏦 Saldo Bancario",
        description=f"El saldo de **{member.display_name}** es de **{balance} 💰**.",
        color=discord.Color.gold()
    )
    return embed

def _get_daily_reward(user_id: int, guild_id: int) -> discord.Embed:
    last_daily = get_last_action_time(user_id, guild_id, 'daily')
    cooldown = ECONOMY_COOLDOWN_DAILY_HOURS * 3600
    current_time = time.time()

    if current_time - last_daily < cooldown:
        remaining_seconds = cooldown - (current_time - last_daily)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        return create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para reclamar tu próxima recompensa diaria.")

    update_balance(user_id, guild_id, DAILY_REWARD)
    set_last_action_time(user_id, guild_id, 'daily')

    return create_success_embed("Recompensa Diaria", f"Has reclamado tu recompensa diaria de **{DAILY_REWARD} 💰**.")

def _do_work(user_id: int, guild_id: int) -> discord.Embed:
    last_work = get_last_action_time(user_id, guild_id, 'work')
    cooldown = ECONOMY_COOLDOWN_WORK_HOURS * 3600
    current_time = time.time()

    if current_time - last_work < cooldown:
        remaining_seconds = cooldown - (current_time - last_work)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        return create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para volver a trabajar.")

    earnings = random.randint(100, 300)
    jobs = ["programar código", "servir café", "pasear perros", "reparar computadoras", "diseñar logos"]
    job = random.choice(jobs)

    update_balance(user_id, guild_id, earnings)
    set_last_action_time(user_id, guild_id, 'work')

    return create_success_embed("Trabajo Realizado", f"Fuiste a **{job}** y ganaste **{earnings} 💰**.")

def _flip_coin(user_id: int, guild_id: int, side: str, amount: int) -> discord.Embed:
    if side not in ['cara', 'cruz']:
        return create_error_embed("Error", "Elige 'cara' o 'cruz'.")
    if amount <= 0:
        return create_error_embed("Error", "La cantidad debe ser positiva.")

    user_balance = get_balance(user_id, guild_id)
    if user_balance < amount:
        return create_error_embed("Error", "No tienes suficiente dinero.")

    result = random.choice(['cara', 'cruz'])

    if result == side:
        update_balance(user_id, guild_id, amount)
        new_balance = user_balance + amount
        return create_success_embed("¡Ganaste!", f"Salió **{result}**. ¡Ganaste **{amount} 💰**! Saldo: {new_balance} 💰")
    else:
        update_balance(user_id, guild_id, -amount)
        new_balance = user_balance - amount
        return create_error_embed("Perdiste", f"Salió **{result}**. Perdiste **{amount} 💰**. Saldo: {new_balance} 💰")

def _play_slots(user_id: int, guild_id: int, amount: int) -> discord.Embed:
    if amount <= 0:
        return create_error_embed("Error", "La cantidad debe ser positiva.")

    user_balance = get_balance(user_id, guild_id)
    if user_balance < amount:
        return create_error_embed("Error", "No tienes suficiente dinero.")

    emojis = ["🍒", "🍇", "🍋", "7️⃣"]
    results = [random.choice(emojis) for _ in range(3)]

    slot_display = f"| **{' | '.join(results)}** |"

    if results[0] == results[1] == results[2]:
        winnings = amount * 7
        update_balance(user_id, guild_id, winnings)
        embed = create_success_embed("¡JACKPOT! 🎰🎰🎰", f"{slot_display}\n¡Ganaste **{winnings} 💰**! (x7)")
    elif results[0] == results[1] or results[1] == results[2]:
        winnings = amount * 2
        update_balance(user_id, guild_id, winnings)
        embed = create_success_embed("¡Doble! 🎰🎰", f"{slot_display}\n¡Ganaste **{winnings} 💰**! (x2)")
    else:
        update_balance(user_id, guild_id, -amount)
        embed = create_error_embed("Perdiste 💸", f"{slot_display}\nPerdiste **{amount} 💰**.")

    new_balance = get_balance(user_id, guild_id)
    embed.set_footer(text=f"Saldo actual: {new_balance} 💰")
    return embed

def _rob_member(user_id: int, guild_id: int, target: discord.Member) -> discord.Embed:
    target_id = target.id
    if user_id == target_id or target.bot:
        return create_error_embed("Error", "No puedes robarte a ti mismo o a un bot.")

    last_rob = get_last_action_time(user_id, guild_id, 'rob')
    cooldown = 2 * 3600 
    current_time = time.time()

    if current_time - last_rob < cooldown:
        remaining_seconds = cooldown - (current_time - last_rob)
        remaining_hours = int(remaining_seconds // 3600)
        remaining_minutes = int((remaining_seconds % 3600) // 60)
        return create_error_embed("En Cooldown", f"Debes esperar **{remaining_hours}h {remaining_minutes}m** para volver a robar.")

    target_balance = get_balance(target_id, guild_id)
    if target_balance < 1000:
        return create_error_embed("Pobreza", f"{target.display_name} es demasiado pobre para ser robado (necesita al menos 1000 💰).")

    set_last_action_time(user_id, guild_id, 'rob') 

    if random.random() < 0.4:
        rob_amount = int(target_balance * random.uniform(0.1, 0.3)) 

        update_balance(user_id, guild_id, rob_amount)
        update_balance(target_id, guild_id, -rob_amount)

        return create_success_embed("¡Robo Exitoso! 😈", f"Le robaste **{rob_amount} 💰** a {target.display_name}. ¡Huye!")
    else:
        fine = random.randint(100, 500)
        update_balance(user_id, guild_id, -fine)

        return create_error_embed("¡Atrapado! 🚨", f"Fuiste atrapado intentando robar a {target.display_name}. Tuviste que pagar una multa de **{fine} 💰**.")

# --- Leveling Helpers ---

def _get_rank_embed(member: discord.Member) -> discord.Embed:
    xp, level, _ = get_level_data(member.id, member.guild.id)
    xp_needed_next = get_xp_needed(level)

    progress = int(xp / xp_needed_next * 10)
    progress_bar = '🟩' * progress + '⬛' * (10 - progress)

    embed = discord.Embed(
        title=f"📊 Rango de {member.display_name}",
        description=f"**Nivel:** {level}\n**XP:** {xp} / {xp_needed_next}\n**Progreso:**\n`{progress_bar}`",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed

def _get_leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    top_users = get_leaderboard_data(guild.id)

    if not top_users:
        return create_error_embed("Error", "No hay datos de nivelación para mostrar.")
async def _propose_marriage(ctx_or_interaction, member: discord.Member):
    user = ctx_or_interaction.author if isinstance(ctx_or_interaction, commands.Context) else ctx_or_interaction.user
    user1_id = user.id
    user2_id = member.id
    guild_id = member.guild.id

    if user1_id == user2_id or member.bot:
        error_embed = create_error_embed("Error", "No puedes casarte contigo mismo o con un bot.")
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send(embed=error_embed, delete_after=10)
        else:
            await ctx_or_interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    if get_partner(user1_id, guild_id) or get_partner(user2_id, guild_id):
        error_embed = create_error_embed("Error", "Uno de los usuarios ya está casado.")
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send(embed=error_embed, delete_after=10)
        else:
            await ctx_or_interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    proposal_embed = discord.Embed(
        title="💍 Propuesta de Matrimonio",
        description=f"{member.mention}, **{user.display_name}** te ha propuesto matrimonio.\n\nReacciona con **✅** para aceptar o **❌** para rechazar.",
        color=discord.Color.light_grey()
    )

    if isinstance(ctx_or_interaction, commands.Context):
        message = await ctx_or_interaction.send(content=member.mention, embed=proposal_embed)
    else:
        await ctx_or_interaction.response.send_message(content=member.mention, embed=proposal_embed)
        message = await ctx_or_interaction.original_response()

    await message.add_reaction("✅")
    await message.add_reaction("❌")

    def check(reaction, u):
        return u.id == user2_id and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == message.id

    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
    except asyncio.TimeoutError:
        expired_embed = create_error_embed("Propuesta Expirada", "La propuesta de matrimonio ha expirado.")
        if isinstance(ctx_or_interaction, commands.Context):
            await message.edit(embed=expired_embed, content=None)
            await message.clear_reactions()
        else:
            await ctx_or_interaction.edit_original_response(embed=expired_embed, content=None, view=None)
        return

    if str(reaction.emoji) == "✅":
        with closing(sqlite3.connect(DB_NAME)) as conn:
            cursor = conn.cursor()
            u1, u2 = sorted([user1_id, user2_id])
            cursor.execute("INSERT INTO marriages (user1_id, user2_id, guild_id, marriage_date) VALUES (?, ?, ?, ?)",
                           (u1, u2, guild_id, datetime.now().isoformat()))
            conn.commit()

        success_embed = create_success_embed("¡BODAS!", f"**{user.display_name}** y **{member.display_name}** ¡se han casado! 🎉")
        if isinstance(ctx_or_interaction, commands.Context):
            await message.edit(embed=success_embed, content=f"{user.mention} {member.mention}")
            await message.clear_reactions()
        else:
            await ctx_or_interaction.edit_original_response(embed=success_embed, content=f"{user.mention} {member.mention}", view=None)
    else:
        rejected_embed = create_error_embed("Propuesta Rechazada", f"**{member.display_name}** ha rechazado la propuesta de matrimonio.")
        if isinstance(ctx_or_interaction, commands.Context):
            await message.edit(embed=rejected_embed, content=None)
            await message.clear_reactions()
        else:
            await ctx_or_interaction.edit_original_response(embed=rejected_embed, content=None, view=None)

async def _initiate_divorce(ctx_or_interaction):
    user = ctx_or_interaction.author if isinstance(ctx_or_interaction, commands.Context) else ctx_or_interaction.user
    guild_id = user.guild.id
    partner_id = get_partner(user.id, guild_id)

    if not partner_id:
        error_embed = create_error_embed("Error", "No estás casado con nadie.")
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send(embed=error_embed, delete_after=10)
        else:
            await ctx_or_interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    partner = user.guild.get_member(partner_id)
    partner_name = partner.display_name if partner else f"Usuario con ID {partner_id}"

    divorce_embed = discord.Embed(
        title="💔 Solicitud de Divorcio",
        description=f"¿Estás seguro de que quieres divorciarte de **{partner_name}**?\n\nReacciona con **💔** para confirmar el divorcio.",
        color=discord.Color.red()
    )

    if isinstance(ctx_or_interaction, commands.Context):
        message = await ctx_or_interaction.send(embed=divorce_embed)
    else:
        await ctx_or_interaction.response.send_message(embed=divorce_embed)
        message = await ctx_or_interaction.original_response()

    await message.add_reaction("💔")

    def check(reaction, u):
        return u.id == user.id and str(reaction.emoji) == "💔" and reaction.message.id == message.id

    try:
        await bot.wait_for('reaction_add', timeout=20.0, check=check)
    except asyncio.TimeoutError:
        expired_embed = create_error_embed("Confirmación Expirada", "La solicitud de divorcio ha expirado.")
        if isinstance(ctx_or_interaction, commands.Context):
            await message.edit(embed=expired_embed, content=None)
            await message.clear_reactions()
        else:
            await ctx_or_interaction.edit_original_response(embed=expired_embed, content=None, view=None)
        return

    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        u1, u2 = sorted([user.id, partner_id])
        cursor.execute("DELETE FROM marriages WHERE user1_id = ? AND user2_id = ? AND guild_id = ?", (u1, u2, guild_id))
        conn.commit()

    success_embed = create_success_embed("Divorcio Consumado", f"**{user.display_name}** se ha divorciado de **{partner_name}**. ¡Libertad!")
    if isinstance(ctx_or_interaction, commands.Context):
        await message.edit(embed=success_embed, content=None)
        await message.clear_reactions()
    else:
        await ctx_or_interaction.edit_original_response(embed=success_embed, content=None, view=None)

async def _show_spouse(ctx_or_interaction):
    user = ctx_or_interaction.author if isinstance(ctx_or_interaction, commands.Context) else ctx_or_interaction.user
    guild_id = user.guild.id
    data = get_marriage_data(user.id, guild_id)

    if not data:
        error_embed = create_error_embed("Matrimonio", "No estás casado con nadie.")
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send(embed=error_embed, delete_after=10)
        else:
            await ctx_or_interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    user1_id, user2_id, date_str = data
    partner_id = user2_id if user1_id == user.id else user1_id
    partner = user.guild.get_member(partner_id)
    partner_name = partner.display_name if partner else f"ID: {partner_id}"

    marriage_date = datetime.fromisoformat(date_str).strftime("%d de %B de %Y")

    spouse_embed = discord.Embed(
        title=f"❤️ Matrimonio de {user.display_name}",
        description=f"Estás casado(a) con **{partner_name}**.",
        color=discord.Color.red()
    )
    spouse_embed.add_field(name="Fecha de Aniversario", value=marriage_date, inline=False)

    if isinstance(ctx_or_interaction, commands.Context):
        await ctx_or_interaction.send(embed=spouse_embed)
    else:
        await ctx_or_interaction.response.send_message(embed=spouse_embed)

# --- Shop Helpers ---

def _shop_embed(guild: discord.Guild) -> discord.Embed:
    shop_roles = get_shop_roles(guild.id)

    if not shop_roles:
        return create_error_embed("Tienda Vacía", "No hay roles a la venta en este momento.")

    description = []
    for role_id, price in shop_roles:
        role = guild.get_role(role_id)
        if role:
            description.append(f"**{role.name}** ➡️ **{price} 💰**")

    embed = discord.Embed(
        title="🛍️ Tienda de Roles",
        description='\n'.join(description) + "\n\nUsa `/buyrole <NombreDelRol>` o `!buyrole <NombreDelRol>` para comprar.",
        color=discord.Color.blue()
    )
    return embed

async def _buy_role_helper(user: discord.Member, role_name: str) -> discord.Embed:
    role_name = role_name.strip()
    shop_roles = get_shop_roles(user.guild.id)

    role_to_buy = None
    price = 0

    for role_id, p in shop_roles:
        role = user.guild.get_role(role_id)
        if role and role.name.lower() == role_name.lower():
            role_to_buy = role
            price = p
            break

    if not role_to_buy:
        return create_error_embed("Error", f"El rol **{role_name}** no está a la venta.")

    if role_to_buy in user.roles:
        return create_error_embed("Error", f"Ya tienes el rol **{role_to_buy.name}**.")

    user_balance = get_balance(user.id, user.guild.id)
    if user_balance < price:
        return create_error_embed("Error", f"No tienes suficiente dinero. Necesitas **{price} 💰**.")

    try:
        update_balance(user.id, user.guild.id, -price)
        await user.add_roles(role_to_buy, reason="Compra de rol en la tienda.")
        new_balance = get_balance(user.id, user.guild.id)

        return create_success_embed("Compra Exitosa", f"Has comprado el rol **{role_to_buy.name}** por **{price} 💰**. Saldo restante: {new_balance} 💰")
    except discord.Forbidden:
        return create_error_embed("Error de Permisos", "No puedo darte ese rol (puede que el rol esté por encima del mío).")
    description = []
    for i, (user_id, xp, level) in enumerate(top_users):
        member = guild.get_member(user_id)
        name = member.display_name if member else f"Usuario Desconocido (ID: {user_id})"

        rank_emoji = ""
        if i == 0: rank_emoji = "🥇"
        elif i == 1: rank_emoji = "🥈"
        elif i == 2: rank_emoji = "🥉"
        else: rank_emoji = f"**#{i+1}**"

        description.append(f"{rank_emoji} **{name}** - Nivel **{level}** ({xp} XP)")

    embed = discord.Embed(
        title="🏆 Tabla de Clasificación del Servidor",
        description='\n'.join(description),
        color=discord.Color.gold()
    )
    return embed

# ----------------------------------------------------
# 10. COMANDOS DE ECONOMÍA Y NIVELES (PREFIX Y SLASH)
# ----------------------------------------------------

@bot.command(name='balance', aliases=['bal'])
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.send(embed=_balance_embed(member))

@bot.command(name='daily')
async def daily(ctx):
    embed = _get_daily_reward(ctx.author.id, ctx.guild.id)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)

@bot.command(name='work')
async def work(ctx):
    embed = _do_work(ctx.author.id, ctx.guild.id)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)

@bot.command(name='flip')
async def flip(ctx, side: str, amount: int):
    embed = _flip_coin(ctx.author.id, ctx.guild.id, side.lower(), amount)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)

@bot.command(name='slots')
async def slots(ctx, amount: int):
    embed = _play_slots(ctx.author.id, ctx.guild.id, amount)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)

@bot.command(name='rob')
async def rob(ctx, member: discord.Member):
    embed = _rob_member(ctx.author.id, ctx.guild.id, member)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)
def _create_invite_embed(user: discord.User, bot_user: discord.ClientUser) -> discord.Embed:
    """Crea el embed de invitación."""
    invite_url = generate_invite_link(CLIENT_ID_INVITE, PERMISSION_CODE_INVITE)
    embed = discord.Embed(
        title="🐾 ¡Adóptame! Enlace de Invitación",
        description=f"**{user.display_name}**, este michi necesita un hogar en tu servidor. ¡Haz clic en el botón para traerme!\n\n**[Invitación de Nexus Bot 🐱]({invite_url})**",
        color=discord.Color.from_rgb(255, 192, 203)
    )
    embed.set_thumbnail(url=bot_user.display_avatar.url)
    embed.set_footer(text="Gracias por querer a este gatito 💖")
    return embed

@bot.command(name='invite')
async def invite_prefix(ctx):
    """Maneja el comando de prefijo !invite."""
    embed = _create_invite_embed(ctx.author, bot.user)
    await ctx.send(embed=embed)


@bot.tree.command(name="invite", description="¡Miau! Consigue el enlace para invitar a este michi a tu servidor.")
async def invite_slash(interaction: discord.Interaction):
    """Maneja el comando de barra diagonal /invite."""
    embed = _create_invite_embed(interaction.user, bot.user)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- COMANDOS DE NIVELACIÓN (PREFIX) ---

def get_leaderboard_data(guild_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, xp, level FROM leveling WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10",
            (guild_id,)
        )
        return cursor.fetchall()

@bot.command(name='rank')
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.send(embed=_get_rank_embed(member))


@bot.command(name='leaderboard', aliases=['top'])
async def leaderboard(ctx):
    embed = _get_leaderboard_embed(ctx.guild)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)

@bot.tree.command(name='balance', description='💰 Muestra tu saldo o el de otro usuario.')
@discord.app_commands.describe(member='El usuario cuyo saldo quieres ver (opcional).')
async def slash_balance(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target_member = member or interaction.user
    await interaction.response.send_message(embed=_balance_embed(target_member))

@bot.tree.command(name='daily', description='🎁 Reclama tu recompensa diaria.')
async def slash_daily(interaction: discord.Interaction):
    embed = _get_daily_reward(interaction.user.id, interaction.guild.id)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

@bot.tree.command(name='work', description='💪 Trabaja para ganar dinero.')
async def slash_work(interaction: discord.Interaction):
    embed = _do_work(interaction.user.id, interaction.guild.id)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

@bot.tree.command(name='flip', description='🪙 Apuesta a cara o cruz.')
@discord.app_commands.describe(side='La cara de la moneda que eliges.', amount='La cantidad de dinero a apostar.')
@discord.app_commands.choices(side=[
    discord.app_commands.Choice(name='Cara', value='cara'),
    discord.app_commands.Choice(name='Cruz', value='cruz')
])
async def slash_flip(interaction: discord.Interaction, side: str, amount: int):
    embed = _flip_coin(interaction.user.id, interaction.guild.id, side, amount)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)


@bot.tree.command(name='slots', description='🎰 Juega a las tragaperras.')
@discord.app_commands.describe(amount='La cantidad de dinero a apostar.')
async def slash_slots(interaction: discord.Interaction, amount: int):
    embed = _play_slots(interaction.user.id, interaction.guild.id, amount)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)


@bot.tree.command(name='rob', description='😈 Intenta robar dinero a otro usuario.')
@discord.app_commands.describe(member='El usuario al que quieres robar.')
async def slash_rob(interaction: discord.Interaction, member: discord.Member):
    embed = _rob_member(interaction.user.id, interaction.guild.id, member)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

@bot.tree.command(name='rank', description='📊 Muestra tu nivel y XP o el de otro usuario.')
@discord.app_commands.describe(member='El usuario cuyo rango quieres ver (opcional).')
async def slash_rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target_member = member or interaction.user
    await interaction.response.send_message(embed=_get_rank_embed(target_member))

@bot.tree.command(name='leaderboard', description='🏆 Muestra la tabla de clasificación de niveles.')
async def slash_leaderboard(interaction: discord.Interaction):
    embed = _get_leaderboard_embed(interaction.guild)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

# --- COMANDOS DE ADMIN DE ECONOMÍA (SLASH) ---
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

    with closing(sqlite3.connect(DB_NAME)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO role_shop (guild_id, role_id, price) VALUES (?, ?, ?)", (interaction.guild.id, role.id, price))
        conn.commit()

    await interaction.response.send_message(embed=create_success_embed("Rol Añadido a la Tienda", f"El rol **{role.name}** está ahora a la venta por **{price} 💰**."), ephemeral=True)


@bot.command(name='shop')
async def shop(ctx):
    embed = _shop_embed(ctx.guild)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)


@bot.command(name='buyrole')
async def buyrole(ctx, *, role_name: str):
    embed = await _buy_role_helper(ctx.author, role_name)
    is_error = "❌" in embed.title
    await ctx.send(embed=embed, delete_after=10 if is_error else None)


@bot.tree.command(name='shop', description='🛍️ Muestra la tienda de roles.')
async def slash_shop(interaction: discord.Interaction):
    embed = _shop_embed(interaction.guild)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

@bot.tree.command(name='buyrole', description='💸 Compra un rol de la tienda.')
@discord.app_commands.describe(role_name='El nombre del rol que quieres comprar.')
async def slash_buyrole(interaction: discord.Interaction, role_name: str):
    embed = await _buy_role_helper(interaction.user, role_name)
    is_error = "❌" in embed.title
    await interaction.response.send_message(embed=embed, ephemeral=is_error)

# ----------------------------------------------------
# 11. INICIO DEL BOT + SERVIDOR WEB PARA RENDER
# ----------------------------------------------------

# Definir la aplicación Flask para el "Keep Alive"
app = Flask('')

@app.route('/')
def home():
    return "¡Hola! Soy Nexus Bot y estoy funcionando correctamente en Render."

def run():
    # Render asigna el puerto en la variable de entorno 'PORT'
    # Si no la encuentra, usa el puerto 8080 por defecto
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN') 
    
    if not TOKEN:
         print("¡ADVERTENCIA! La variable DISCORD_TOKEN no está configurada. El bot no se conectará.")
    else:
        print("Iniciando servidor web Keep-Alive...")
        keep_alive()
        
    
        print("Conectando el bot a Discord...")
        try:
            bot.run(TOKEN)
        except discord.HTTPException:
            print("ERROR: Token inválido o problema de conexión. Revisa tu Token.")
        except Exception as e:
            print(f"Ocurrió un error inesperado al iniciar el bot: {e}")
