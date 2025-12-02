import discord
from discord.ext import commands, tasks
import psycopg2 # Cambiado de sqlite3
import random
import time
from datetime import datetime, timedelta
import asyncio
from typing import Optional
import os
from flask import Flask
from threading import Thread

# ----------------------------------------------------
# 1. CLASE DE AYUDA PERSONALIZADA
# ----------------------------------------------------

class CustomHelpCommand(commands.HelpCommand):
    async def send_bot_help(self, mapping):
        ctx = self.context
        prefix = ctx.prefix 
        embed = discord.Embed(
            title="📚 Guía de Comandos del Bot",
            description=f"Aquí está la lista de comandos disponibles. Recuerda usar el prefijo `{prefix}`.",
            color=discord.Color.blurple()
        )
        # (Resumen de comandos mantenido igual para ahorrar espacio visual, funciona igual)
        embed.add_field(name="🛡️ Moderación", value="Ban, Kick, Mute, Warn, Purge", inline=False)
        embed.add_field(name="💰 Economía", value="Balance, Daily, Work, Flip, Shop, Rank", inline=False)
        embed.add_field(name="❤️ Bodas", value="Marry, Divorce, Spouse", inline=False)
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

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)
bot.help_command = CustomHelpCommand()

# ----------------------------------------------------
# 3. FUNCIONES DE CONEXIÓN A LA BASE DE DATOS (POSTGRESQL)
# ----------------------------------------------------

def get_db_connection():
    """Establece conexión con Neon PostgreSQL."""
    try:
        DATABASE_URL = os.getenv('DATABASE_URL')
        if not DATABASE_URL:
            print("ERROR: No se encontró la variable DATABASE_URL.")
            return None
        
        # connect_timeout=10' para que no tarde mucho en fallar si hay error
        conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10) 
        return conn
    except psycopg2.OperationalError as e:
        print(f"ERROR DE CONEXIÓN CRÍTICO: No se pudo conectar a Neon. Revise su DATABASE_URL. Detalle: {e}") 
        return None
    except Exception as e:
        print(f"Error conectando a la base de datos (general): {e}")
        return None

def initialize_db():
    """Inicializa las tablas en PostgreSQL."""
    conn = get_db_connection()
    if not conn: return
    
    try:
        cur = conn.cursor()
        
        # Tabla Configuración
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                guild_id BIGINT PRIMARY KEY,
                log_channel_id BIGINT,
                report_channel_id BIGINT,
                report_role_id BIGINT,
                autorole_id BIGINT
            )
        """)

        # Tabla Temp Mutes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_mutes (
                user_id BIGINT,
                guild_id BIGINT,
                unmute_time REAL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        # Tabla Warnings (SERIAL es el AUTOINCREMENT de Postgres)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                guild_id BIGINT,
                moderator_id BIGINT,
                reason TEXT,
                timestamp TEXT
            )
        """)

        # Tabla Economía
        cur.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                user_id BIGINT,
                guild_id BIGINT,
                balance INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        # Tabla Cooldowns
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id BIGINT,
                guild_id BIGINT,
                action TEXT,
                last_time REAL,
                PRIMARY KEY (user_id, guild_id, action)
            )
        """)

        # Tabla Leveling
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leveling (
                user_id BIGINT,
                guild_id BIGINT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_message_time REAL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        # Tabla Role Shop
        cur.execute("""
            CREATE TABLE IF NOT EXISTS role_shop (
                guild_id BIGINT,
                role_id BIGINT,
                price INTEGER,
                PRIMARY KEY (guild_id, role_id)
            )
        """)

        # Tabla Marriages
        cur.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user1_id BIGINT,
                user2_id BIGINT,
                guild_id BIGINT,
                marriage_date TEXT,
                PRIMARY KEY (user1_id, user2_id, guild_id)
            )
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("Base de datos PostgreSQL inicializada correctamente.")
    except Exception as e:
        print(f"Error inicializando DB: {e}")

# ----------------------------------------------------
# 4. FUNCIONES DE UTILIDAD (MODIFICADAS PARA POSTGRES)
# ----------------------------------------------------
# Nota: Postgres usa %s en lugar de ? para placeholders.
# Nota 2: Postgres no tiene "INSERT OR IGNORE", usa "ON CONFLICT DO NOTHING"

# --- Configuración ---
def get_config(guild):
    conn = get_db_connection()
    if not conn: return {}
    cur = conn.cursor()
    cur.execute("SELECT log_channel_id, report_channel_id, report_role_id, autorole_id FROM config WHERE guild_id = %s", (guild.id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    if result:
        return {'log_channel_id': result[0], 'report_channel_id': result[1], 'report_role_id': result[2], 'autorole_id': result[3]}
    return {}

def get_log_channel(guild):
    config = get_config(guild)
    if config.get('log_channel_id'):
        return guild.get_channel(config['log_channel_id'])
    return None

def get_report_config(guild):
    config = get_config(guild)
    return guild.get_channel(config.get('report_channel_id')), guild.get_role(config.get('report_role_id'))

# --- Economía ---
def get_balance(user_id, guild_id):
    conn = get_db_connection()
    cur = conn.cursor()
    # Inicializar si no existe
    cur.execute("INSERT INTO economy (user_id, guild_id) VALUES (%s, %s) ON CONFLICT (user_id, guild_id) DO NOTHING", (user_id, guild_id))
    conn.commit()
    cur.execute("SELECT balance FROM economy WHERE user_id = %s AND guild_id = %s", (user_id, guild_id))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result[0] if result else 0

def update_balance(user_id, guild_id, amount):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO economy (user_id, guild_id) VALUES (%s, %s) ON CONFLICT (user_id, guild_id) DO NOTHING", (user_id, guild_id))
    cur.execute("UPDATE economy SET balance = balance + %s WHERE user_id = %s AND guild_id = %s", (amount, user_id, guild_id))
    conn.commit()
    cur.close()
    conn.close()

def set_balance(user_id, guild_id, amount):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO economy (user_id, guild_id, balance) VALUES (%s, %s, %s)
        ON CONFLICT (user_id, guild_id) DO UPDATE SET balance = EXCLUDED.balance
    """, (user_id, guild_id, amount))
    conn.commit()
    cur.close()
    conn.close()
    return amount

# --- Cooldowns ---
def get_last_action_time(user_id, guild_id, action):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_time FROM cooldowns WHERE user_id = %s AND guild_id = %s AND action = %s", (user_id, guild_id, action))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result[0] if result else 0.0

def set_last_action_time(user_id, guild_id, action):
    current_time = time.time()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cooldowns (user_id, guild_id, action, last_time) VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, guild_id, action) DO UPDATE SET last_time = EXCLUDED.last_time
    """, (user_id, guild_id, action, current_time))
    conn.commit()
    cur.close()
    conn.close()

# --- Nivelación ---
def get_level_data(user_id, guild_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO leveling (user_id, guild_id) VALUES (%s, %s) ON CONFLICT (user_id, guild_id) DO NOTHING", (user_id, guild_id))
    conn.commit()
    cur.execute("SELECT xp, level, last_message_time FROM leveling WHERE user_id = %s AND guild_id = %s", (user_id, guild_id))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result if result else (0, 0, 0.0)

def get_xp_needed(level):
    return 100 + level * 50

def update_level_data(user_id, guild_id, xp_to_add, last_message_time):
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

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE leveling SET xp = %s, level = %s, last_message_time = %s WHERE user_id = %s AND guild_id = %s",
        (new_xp, new_level, last_message_time, user_id, guild_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return new_level, leveled_up

# --- Tienda y Bodas ---
def get_shop_roles(guild_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT role_id, price FROM role_shop WHERE guild_id = %s", (guild_id,))
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result

def get_partner(user_id, guild_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user2_id, user1_id FROM marriages WHERE (user1_id = %s OR user2_id = %s) AND guild_id = %s", (user_id, user_id, guild_id))
    result = cur.fetchone()
    cur.close()
    conn.close()
    if result:
        return result[0] if result[0] != user_id else result[1]
    return None

def get_marriage_data(user_id, guild_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user1_id, user2_id, marriage_date FROM marriages WHERE (user1_id = %s OR user2_id = %s) AND guild_id = %s", (user_id, user_id, guild_id))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result

# ----------------------------------------------------
# 5. UTILIDADES VISUALES (Embeds)
# ----------------------------------------------------
def create_error_embed(title: str, description: str):
    return discord.Embed(title=f"❌ {title}", description=description, color=discord.Color.red())

def create_success_embed(title: str, description: str):
    return discord.Embed(title=f"✅ {title}", description=description, color=discord.Color.green())

async def create_role_if_not_exists(guild, role_name):
    role = discord.utils.get(guild.roles, name=role_name)
    if role: return role
    role = await guild.create_role(name=role_name, permissions=discord.Permissions.none())
    if role_name == MUTE_ROLE_NAME:
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                try:
                    await channel.set_permissions(role, send_messages=False, speak=False)
                except: pass
    return role

# ----------------------------------------------------
# 6. EVENTOS Y TAREAS
# ----------------------------------------------------

@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user.name} (ID: {bot.user.id})')
    initialize_db()
    try:
        await bot.tree.sync()
        print("Comandos Slash sincronizados.")
    except Exception as e:
        print(f"Error sync: {e}")
    check_mutes.start()

@tasks.loop(minutes=1)
async def check_mutes():
    current_time = time.time()
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    cur.execute("SELECT user_id, guild_id FROM temp_mutes WHERE unmute_time <= %s", (current_time,))
    expired_mutes = cur.fetchall()
    
    for user_id, guild_id in expired_mutes:
        guild = bot.get_guild(guild_id)
        if not guild: continue
        member = guild.get_member(user_id)
        mute_role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
        if member and mute_role and mute_role in member.roles:
            try:
                await member.remove_roles(mute_role, reason="Expirado")
            except: pass
        
        # Borrar de DB
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM temp_mutes WHERE user_id = %s AND guild_id = %s", (user_id, guild_id))
        conn.commit()
        cur2.close()
    
    cur.close()
    conn.close()

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    # Sistema XP
    user_id = message.author.id
    guild_id = message.guild.id
    current_time = time.time()
    xp, level, last_msg = get_level_data(user_id, guild_id)
    
    if current_time - last_msg >= XP_COOLDOWN_SECONDS:
        new_level, leveled_up = update_level_data(user_id, guild_id, XP_PER_MESSAGE, current_time)
        if leveled_up:
            await message.channel.send(f"🎉 ¡{message.author.mention} subió al Nivel **{new_level}**!")

    await bot.process_commands(message)

# ----------------------------------------------------
# 7. COMANDOS (Resumen adaptado a Postgres)
# ----------------------------------------------------

# Logs
@bot.tree.command(name='admin-setlogs')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO config (guild_id, log_channel_id) VALUES (%s, %s)
        ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id
    """, (interaction.guild.id, channel.id))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(embed=create_success_embed("Configurado", f"Canal de logs: {channel.mention}"))

# Report
@bot.tree.command(name='admin-setreport')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setreport(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role = None):
    role_id = role.id if role else None
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO config (guild_id, report_channel_id, report_role_id) VALUES (%s, %s, %s)
        ON CONFLICT (guild_id) DO UPDATE SET report_channel_id = EXCLUDED.report_channel_id, report_role_id = EXCLUDED.report_role_id
    """, (interaction.guild.id, channel.id, role_id))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(embed=create_success_embed("Reportes", f"Canal: {channel.mention}"))

# Warn
@bot.tree.command(name='mod-warn')
@discord.app_commands.checks.has_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = 'N/A'):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO warnings (user_id, guild_id, moderator_id, reason, timestamp) VALUES (%s, %s, %s, %s, %s)",
                (member.id, interaction.guild.id, interaction.user.id, reason, datetime.now().isoformat()))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(embed=create_success_embed("Advertencia", f"Usuario {member.mention} advertido."))

# Mute (Ejemplo abreviado, usa la lógica ya definida arriba)
@bot.tree.command(name='mod-mute')
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = 'N/A'):
    # (Lógica de tiempo igual al original...)
    try:
        if 'd' in duration: delta = timedelta(days=int(duration[:-1]))
        elif 'h' in duration: delta = timedelta(hours=int(duration[:-1]))
        elif 'm' in duration: delta = timedelta(minutes=int(duration[:-1]))
        else: raise ValueError
    except:
        return await interaction.response.send_message("Formato inválido (ej: 1h, 30m)", ephemeral=True)

    unmute_ts = time.time() + delta.total_seconds()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO temp_mutes (user_id, guild_id, unmute_time) VALUES (%s, %s, %s)
        ON CONFLICT (user_id, guild_id) DO UPDATE SET unmute_time = EXCLUDED.unmute_time
    """, (member.id, interaction.guild.id, unmute_ts))
    conn.commit()
    cur.close()
    conn.close()
    
    mute_role = await create_role_if_not_exists(interaction.guild, MUTE_ROLE_NAME)
    await member.add_roles(mute_role, reason=reason)
    await interaction.response.send_message(embed=create_success_embed("Muteado", f"{member.mention} por {duration}."))

# Economy Admin
@bot.tree.command(name='admin-setmoney')
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    new_bal = set_balance(member.id, interaction.guild.id, amount)
    await interaction.response.send_message(embed=create_success_embed("Dinero", f"Saldo de {member.mention}: {new_bal}"))

# Marry
@bot.command(name='marry')
async def marry(ctx, member: discord.Member):
    user1, user2 = ctx.author.id, member.id
    if user1 == user2 or member.bot: return await ctx.send("No puedes casarte contigo o con bots.")
    if get_partner(user1, ctx.guild.id) or get_partner(user2, ctx.guild.id):
        return await ctx.send("¡Alguien ya está casado!")

    msg = await ctx.send(f"{member.mention}, ¿aceptas a {ctx.author.mention}? (✅/❌)")
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(rxn, usr): return usr.id == user2 and str(rxn.emoji) in ["✅", "❌"] and rxn.message.id == msg.id
    
    try:
        rxn, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
        if str(rxn.emoji) == "✅":
            conn = get_db_connection()
            cur = conn.cursor()
            u1, u2 = sorted([user1, user2])
            cur.execute("INSERT INTO marriages (user1_id, user2_id, guild_id, marriage_date) VALUES (%s, %s, %s, %s)",
                        (u1, u2, ctx.guild.id, datetime.now().isoformat()))
            conn.commit()
            cur.close()
            conn.close()
            await ctx.send(f"🎉 ¡{ctx.author.mention} y {member.mention} se han casado!")
        else:
            await ctx.send("Propuesta rechazada 💔")
    except:
        await ctx.send("Tiempo agotado.")

# Invite Link
CLIENT_ID_INVITE = "1443693871061008457"  
@bot.tree.command(name="invite")
async def invite_slash(interaction: discord.Interaction):
    url = f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID_INVITE}&scope=bot%20applications.commands&permissions=8"
    await interaction.response.send_message(f"[Invitar Bot]({url})", ephemeral=True)

# ----------------------------------------------------
# 8. SERVIDOR WEB (KEEP ALIVE)
# ----------------------------------------------------

app = Flask('')

@app.route('/')
def home():
    return "Bot Online con PostgreSQL."

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ----------------------------------------------------
# 9. INICIO
# ----------------------------------------------------

if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("Falta DISCORD_TOKEN")
    else:
        keep_alive()
        bot.run(TOKEN)
