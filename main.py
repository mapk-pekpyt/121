# main.py - VPN HOSTING БОТ С ПОДДЕРЖКОЙ РАЗНЫХ VPN
import os, asyncio, logging, sys, random, qrcode, io, sqlite3, re, subprocess, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, InlineKeyboardButton, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh, aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
ADMIN_CHAT_ID = -1003542769962
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_USERNAME = "@vpnbothost"
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("""CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, ssh_key TEXT NOT NULL, connection_string TEXT NOT NULL, vpn_type TEXT DEFAULT 'wireguard', max_users INTEGER DEFAULT 50, current_users INTEGER DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, server_ip TEXT, public_key TEXT, wireguard_configured BOOLEAN DEFAULT FALSE, openvpn_configured BOOLEAN DEFAULT FALSE, ikev2_configured BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, server_id INTEGER, client_name TEXT, client_public_key TEXT, client_ip TEXT, config_data TEXT, config_file_path TEXT, qr_code_path TEXT, device_type TEXT DEFAULT 'auto', subscription_end TIMESTAMP, trial_used BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount REAL NOT NULL, currency TEXT DEFAULT 'USD', payment_method TEXT, period_days INTEGER, status TEXT DEFAULT 'pending', telegram_payment_id TEXT, subscription_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY, week_price INTEGER DEFAULT 50, month_price INTEGER DEFAULT 150, week_usd REAL DEFAULT 5.0, month_usd REAL DEFAULT 15.0)")
            await db.execute("INSERT OR IGNORE INTO prices (id, week_price, month_price, week_usd, month_usd) VALUES (1, 50, 150, 5.0, 15.0)")
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    if chat_id: return user_id == ADMIN_ID or str(chat_id) == str(ADMIN_CHAT_ID)
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price, week_usd, month_usd FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices: return {"week": {"days": 7, "stars": prices[0], "usd": prices[2]}, "month": {"days": 30, "stars": prices[1], "usd": prices[3]}}
    except: pass
    return {"week": {"days": 7, "stars": 50, "usd": 5.0}, "month": {"days": 30, "stars": 150, "usd": 15.0}}

async def check_ssh_connection(server_id: int = None, conn_str: str = None, ssh_key: str = None):
    """Проверка SSH подключения"""
    try:
        if server_id:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
                server = await cursor.fetchone()
                if not server: return False, "Сервер не найден", None
                conn_str, ssh_key = server
        
        try:
            if ':' in conn_str: user_host, port = conn_str.rsplit(':', 1); user, host = user_host.split('@'); port = int(port)
            else: user, host = conn_str.split('@'); port = 22
        except: return False, f"Неверный формат: {conn_str}", None
        
        import tempfile, stat
        ssh_key_clean = ssh_key.strip()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
            f.write(ssh_key_clean); temp_key_path = f.name
        os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
        
        try:
            async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=30) as conn:
                result = await conn.run("whoami && pwd && echo 'SSH_CHECK_OK'", timeout=30)
                if result.exit_status != 0 or 'SSH_CHECK_OK' not in result.stdout:
                    return False, f"Базовые команды не выполняются: {result.stderr}", None
                
                sudo_check = await conn.run("sudo -n true 2>&1; echo $?", timeout=10)
                has_sudo = sudo_check.stdout.strip() == '0'
                is_root = 'root' in result.stdout
                
                os_info = await conn.run("cat /etc/os-release 2>/dev/null || uname -a", timeout=10)
                kernel_check = await conn.run("uname -r", timeout=10)
                
                system_info = {
                    'has_sudo': has_sudo,
                    'is_root': is_root,
                    'os_info': os_info.stdout,
                    'kernel': kernel_check.stdout.strip(),
                    'user': user,
                    'host': host
                }
                
                try: os.unlink(temp_key_path)
                except: pass
                return True, "SSH подключение работает", system_info
                
        except asyncssh.Error as e:
            try: os.unlink(temp_key_path)
            except: pass
            return False, f"SSH ошибка: {str(e)}", None
    except Exception as e:
        return False, f"Общая ошибка: {str(e)}", None

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = False) -> Tuple[str, str, bool]:
    """Выполнение SSH команды"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return "", "Сервер не найден", False
            conn_str, ssh_key = server
            
            try:
                if ':' in conn_str: user_host, port = conn_str.rsplit(':', 1); user, host = user_host.split('@'); port = int(port)
                else: user, host = conn_str.split('@'); port = 22
            except: return "", f"Неверный формат: {conn_str}", False
            
            import tempfile, stat
            ssh_key_clean = ssh_key.strip()
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean); temp_key_path = f.name
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=timeout) as conn:
                    if use_sudo: command = f"sudo {command}"
                    result = await conn.run(command, timeout=timeout)
                    
                    try: os.unlink(temp_key_path)
                    except: pass
                    
                    if result.exit_status == 0:
                        return result.stdout, result.stderr, True
                    else:
                        return result.stdout, result.stderr, False
                    
            except asyncssh.Error as e:
                try: os.unlink(temp_key_path)
                except: pass
                return "", f"SSH ошибка: {str(e)}", False
    except Exception as e:
        return "", f"Ошибка выполнения: {str(e)}", False

# ========== VPN УСТАНОВКИ ==========
async def setup_vpn_auto(server_id: int, vpn_type: str, message: Message):
    """АВТОМАТИЧЕСКАЯ установка выбранного VPN"""
    await message.answer(f"🚀 Начинаю автоматическую установку {vpn_type}...")
    
    # Проверка SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}\nУстановка отменена.")
        return False
    
    await message.answer(f"✅ SSH подключение работает")
    
    # Проверка прав
    if not system_info['has_sudo'] and not system_info['is_root']:
        await message.answer("❌ Нет прав sudo/root. Установка невозможна.")
        return False
    
    if vpn_type == "WireGuard":
        return await setup_wireguard_auto(server_id, message, system_info)
    elif vpn_type == "OpenVPN":
        return await setup_openvpn_auto(server_id, message, system_info)
    elif vpn_type == "IPSec/IKEv2":
        return await setup_ikev2_auto(server_id, message, system_info)
    elif vpn_type == "StrongSwan":
        return await setup_strongswan_auto(server_id, message, system_info)
    elif vpn_type == "Libreswan":
        return await setup_libreswan_auto(server_id, message, system_info)
    else:
        await message.answer(f"❌ Неподдерживаемый тип VPN: {vpn_type}")
        return False

async def setup_wireguard_auto(server_id: int, message: Message, system_info: dict):
    """Автоустановка WireGuard"""
    try:
        # Определяем пакетный менеджер
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            pkg_cmd = "apt-get update && apt-get install -y wireguard wireguard-tools qrencode"
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            pkg_cmd = "yum install -y epel-release && yum install -y wireguard-tools qrencode || dnf install -y wireguard-tools qrencode"
        else:
            await message.answer("❌ Неподдерживаемая ОС для автоустановки WireGuard")
            return False
        
        await message.answer("📦 Устанавливаю WireGuard...")
        stdout, stderr, success = await execute_ssh_command(server_id, pkg_cmd, timeout=300, use_sudo=True)
        if not success:
            await message.answer(f"❌ Ошибка установки: {stderr[:200]}")
            return False
        
        # Настройка WireGuard
        await message.answer("⚙️ Настраиваю WireGuard...")
        setup_cmds = [
            "mkdir -p /etc/wireguard && cd /etc/wireguard",
            "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
            """cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF""",
            "wg-quick up wg0",
            "systemctl enable wg-quick@wg0"
        ]
        
        for cmd in setup_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
            if not success and 'wg-quick up' not in cmd:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        # Получаем публичный ключ
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key", use_sudo=True)
        if success and stdout.strip():
            public_key = stdout.strip()
            
            # Получаем IP сервера
            stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
            server_ip = stdout.strip() if success else ""
            
            # Сохраняем в БД
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET wireguard_configured = TRUE, public_key = ?, server_ip = ? WHERE id = ?", 
                               (public_key, server_ip, server_id))
                await db.commit()
            
            await message.answer(f"✅ WireGuard успешно установлен!\n🔑 Публичный ключ: {public_key[:50]}...\n🌐 IP: {server_ip}")
            return True
        
        await message.answer("❌ Не удалось получить публичный ключ")
        return False
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки WireGuard: {str(e)}")
        return False

async def setup_openvpn_auto(server_id: int, message: Message, system_info: dict):
    """Автоустановка OpenVPN"""
    try:
        await message.answer("📦 Устанавливаю OpenVPN...")
        
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "apt-get install -y openvpn easy-rsa",
                "cp -r /usr/share/easy-rsa/ /etc/openvpn/easy-rsa",
                "cd /etc/openvpn/easy-rsa && ./easyrsa init-pki",
                "cd /etc/openvpn/easy-rsa && echo 'ca' | ./easyrsa build-ca nopass",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa gen-req server nopass",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa sign-req server server",
                "cd /etc/openvpn/easy-rsa && ./easyrsa gen-dh",
                "openvpn --genkey --secret /etc/openvpn/ta.key"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release",
                "yum install -y openvpn easy-rsa",
                "cp -r /usr/share/easy-rsa/3.0.8/ /etc/openvpn/easy-rsa || cp -r /usr/share/easy-rsa/ /etc/openvpn/easy-rsa",
                "cd /etc/openvpn/easy-rsa && ./easyrsa init-pki",
                "cd /etc/openvpn/easy-rsa && echo 'ca' | ./easyrsa build-ca nopass",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa gen-req server nopass",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa sign-req server server",
                "cd /etc/openvpn/easy-rsa && ./easyrsa gen-dh",
                "openvpn --genkey --secret /etc/openvpn/ta.key"
            ]
        else:
            await message.answer("❌ Неподдерживаемая ОС для OpenVPN")
            return False
        
        for cmd in cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        # Создаем конфиг сервера
        server_conf = """port 1194
proto udp
dev tun
ca /etc/openvpn/easy-rsa/pki/ca.crt
cert /etc/openvpn/easy-rsa/pki/issued/server.crt
key /etc/openvpn/easy-rsa/pki/private/server.key
dh /etc/openvpn/easy-rsa/pki/dh.pem
server 10.8.0.0 255.255.255.0
ifconfig-pool-persist /var/log/openvpn/ipp.txt
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 8.8.8.8"
push "dhcp-option DNS 8.8.4.4"
keepalive 10 120
tls-auth /etc/openvpn/ta.key 0
cipher AES-256-CBC
persist-key
persist-tun
status /var/log/openvpn/openvpn-status.log
verb 3
explicit-exit-notify 1"""
        
        create_conf_cmd = f'''cat > /etc/openvpn/server.conf << 'EOF'
{server_conf}
EOF'''
        
        stdout, stderr, success = await execute_ssh_command(server_id, create_conf_cmd, use_sudo=True)
        
        # Включаем IP forwarding
        ip_forward_cmds = [
            "sysctl -w net.ipv4.ip_forward=1",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
            "sysctl -p"
        ]
        
        for cmd in ip_forward_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Запускаем OpenVPN
        startup_cmds = [
            "systemctl start openvpn@server",
            "systemctl enable openvpn@server"
        ]
        
        for cmd in startup_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET openvpn_configured = TRUE, server_ip = ? WHERE id = ?", 
                           (server_ip, server_id))
            await db.commit()
        
        await message.answer(f"✅ OpenVPN успешно установлен!\n🌐 IP: {server_ip}\n📡 Порт: 1194 (UDP)")
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки OpenVPN: {str(e)}")
        return False

async def setup_ikev2_auto(server_id: int, message: Message, system_info: dict):
    """Автоустановка IPSec/IKEv2"""
    try:
        await message.answer("📦 Устанавливаю IPSec/IKEv2...")
        
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "apt-get install -y strongswan strongswan-pki libcharon-extra-plugins",
                "ipsec stop",
                "mkdir -p /etc/ipsec.d/private",
                "chmod 700 /etc/ipsec.d/private"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release",
                "yum install -y strongswan strongswan-pki",
                "systemctl stop strongswan",
                "mkdir -p /etc/strongswan/ipsec.d/private",
                "chmod 700 /etc/strongswan/ipsec.d/private"
            ]
        else:
            await message.answer("❌ Неподдерживаемая ОС для IPSec/IKEv2")
            return False
        
        for cmd in cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        # Генерация сертификатов
        cert_cmds = [
            "pki --gen --type rsa --size 2048 --outform pem > /etc/ipsec.d/private/ca-key.pem",
            "pki --self --ca --lifetime 3650 --in /etc/ipsec.d/private/ca-key.pem --type rsa --dn 'CN=VPN CA' --outform pem > /etc/ipsec.d/cacert.pem",
            "pki --gen --type rsa --size 2048 --outform pem > /etc/ipsec.d/private/server-key.pem",
            "pki --pub --in /etc/ipsec.d/private/server-key.pem --type rsa | pki --issue --lifetime 1825 --cacert /etc/ipsec.d/cacert.pem --cakey /etc/ipsec.d/private/ca-key.pem --dn 'CN=vpn.example.com' --san vpn.example.com --flag serverAuth --flag ikeIntermediate --outform pem > /etc/ipsec.d/certs/server-cert.pem"
        ]
        
        for cmd in cert_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Настройка конфига
        ikev2_conf = """config setup
  charondebug="ike 1, knl 1, cfg 0"
  uniqueids=no

conn ikev2-vpn
  auto=add
  compress=no
  type=tunnel
  keyexchange=ikev2
  fragmentation=yes
  forceencaps=yes
  dpdaction=clear
  dpddelay=300s
  rekey=no
  left=%any
  leftid=@vpn.example.com
  leftcert=server-cert.pem
  leftsendcert=always
  leftsubnet=0.0.0.0/0
  right=%any
  rightid=%any
  rightauth=eap-mschapv2
  rightsourceip=10.10.10.0/24
  rightdns=8.8.8.8,8.8.4.4
  rightsendcert=never
  eap_identity=%identity"""
        
        create_conf_cmd = f'''cat > /etc/ipsec.conf << 'EOF'
{ikev2_conf}
EOF'''
        
        stdout, stderr, success = await execute_ssh_command(server_id, create_conf_cmd, use_sudo=True)
        
        # Настройка секретов
        secrets_conf = """: RSA server-key.pem
vpnuser : EAP "password"
"""
        
        create_secrets_cmd = f'''cat > /etc/ipsec.secrets << 'EOF'
{secrets_conf}
EOF'''
        
        stdout, stderr, success = await execute_ssh_command(server_id, create_secrets_cmd, use_sudo=True)
        
        # Запуск службы
        startup_cmds = [
            "sysctl -w net.ipv4.ip_forward=1",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
            "sysctl -p",
            "ipsec start",
            "systemctl enable strongswan || systemctl enable ipsec"
        ]
        
        for cmd in startup_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET ikev2_configured = TRUE, server_ip = ? WHERE id = ?", 
                           (server_ip, server_id))
            await db.commit()
        
        await message.answer(f"✅ IPSec/IKEv2 успешно установлен!\n🌐 IP: {server_ip}\n🔑 Логин: vpnuser\n🔑 Пароль: password")
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки IPSec/IKEv2: {str(e)}")
        return False

async def setup_strongswan_auto(server_id: int, message: Message, system_info: dict):
    """Автоустановка StrongSwan"""
    return await setup_ikev2_auto(server_id, message, system_info)

async def setup_libreswan_auto(server_id: int, message: Message, system_info: dict):
    """Автоустановка Libreswan"""
    try:
        await message.answer("📦 Устанавливаю Libreswan...")
        
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "apt-get install -y libreswan",
                "ipsec stop",
                "mkdir -p /etc/ipsec.d/private",
                "chmod 700 /etc/ipsec.d/private"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release",
                "yum install -y libreswan",
                "systemctl stop ipsec",
                "mkdir -p /etc/ipsec.d/private",
                "chmod 700 /etc/ipsec.d/private"
            ]
        else:
            await message.answer("❌ Неподдерживаемая ОС для Libreswan")
            return False
        
        for cmd in cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        await message.answer("✅ Libreswan установлен! Настройка аналогична IPSec/IKEv2.")
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки Libreswan: {str(e)}")
        return False

async def setup_vpn_via_git(server_id: int, vpn_type: str, git_repo: str, message: Message):
    """Ручная установка VPN через Git"""
    await message.answer(f"🔧 Начинаю ручную установку {vpn_type} через Git...")
    
    # Проверка SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}")
        return False
    
    if not system_info['has_sudo']:
        await message.answer("❌ Требуются права sudo для ручной установки")
        return False
    
    try:
        # Установка зависимостей
        await message.answer("📦 Устанавливаю зависимости...")
        os_lower = system_info['os_info'].lower()
        
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            deps_cmd = "apt-get update && apt-get install -y git build-essential autoconf libtool pkg-config"
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            deps_cmd = "yum install -y git gcc make autoconf libtool pkgconfig"
        else:
            deps_cmd = "echo 'Установите зависимости вручную'"
        
        stdout, stderr, success = await execute_ssh_command(server_id, deps_cmd, timeout=180, use_sudo=True)
        
        # Клонирование и компиляция
        await message.answer("🔨 Компилирую из исходников...")
        compile_cmds = [
            f"cd /tmp && rm -rf vpn-source 2>/dev/null || true",
            f"cd /tmp && git clone {git_repo} vpn-source",
            "cd /tmp/vpn-source && ./autogen.sh 2>/dev/null || true",
            "cd /tmp/vpn-source && ./configure",
            "cd /tmp/vpn-source && make -j$(nproc)",
            "cd /tmp/vpn-source && make install"
        ]
        
        for cmd in compile_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=300, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        await message.answer(f"✅ {vpn_type} успешно установлен через Git!\n\nТребуется дополнительная ручная настройка.")
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки: {str(e)}")
        return False

async def create_vpn_client(server_id: int, user_id: int, username: str, vpn_type: str, device_type: str = "auto"):
    """Создание клиентской конфигурации"""
    try:
        # Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT server_ip, current_users, max_users FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return None, "Сервер не найден"
            
            server_ip, current_users, max_users = server
            if current_users >= max_users:
                return None, "Сервер переполнен"
            
            client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
            
            if vpn_type == "WireGuard":
                return await create_wireguard_client(server_id, client_name, username, device_type)
            elif vpn_type == "OpenVPN":
                return await create_openvpn_client(server_id, client_name, username, device_type)
            elif vpn_type in ["IPSec/IKEv2", "StrongSwan", "Libreswan"]:
                return await create_ikev2_client(server_id, client_name, username, device_type)
            else:
                return None, f"Неподдерживаемый тип VPN: {vpn_type}"
                
    except Exception as e:
        return None, f"Ошибка создания клиента: {str(e)}"

async def create_wireguard_client(server_id: int, client_name: str, username: str, device_type: str):
    """Создание клиента WireGuard"""
    try:
        # Получаем публичный ключ сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key", use_sudo=True)
        if not success or not stdout.strip():
            return None, "Не удалось получить публичный ключ сервера"
        
        server_pub_key = stdout.strip()
        
        # Генерируем ключи клиента
        keygen_cmds = [
            f"cd /etc/wireguard && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public",
            f"cd /etc/wireguard && cat {client_name}.private",
            f"cd /etc/wireguard && cat {client_name}.public"
        ]
        
        private_key = None
        client_pub_key = None
        
        for i, cmd in enumerate(keygen_cmds):
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
            if not success:
                return None, f"Ошибка генерации ключей: {stderr}"
            if i == 1: private_key = stdout.strip()
            if i == 2: client_pub_key = stdout.strip()
        
        if not private_key or not client_pub_key:
            return None, "Не удалось получить ключи"
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Определяем IP клиента
        stdout, stderr, success = await execute_ssh_command(server_id, "grep -c '^\\[Peer\\]' /etc/wireguard/wg0.conf 2>/dev/null || echo '0'")
        peer_count = int(stdout.strip()) if success else 0
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # Добавляем пира в конфиг
        add_peer_cmd = f"""
        cd /etc/wireguard
        echo '' >> wg0.conf
        echo '[Peer]' >> wg0.conf
        echo '# {username}' >> wg0.conf
        echo 'PublicKey = {client_pub_key}' >> wg0.conf
        echo 'AllowedIPs = {client_ip}/32' >> wg0.conf
        wg set wg0 peer {client_pub_key} allowed-ips {client_ip}/32
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, add_peer_cmd, use_sudo=True)
        
        # Создаем конфиг клиента
        client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}/24
DNS = 8.8.8.8

[Peer]
PublicKey = {server_pub_key}
Endpoint = {server_ip}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        
        # Сохраняем конфиг
        config_filename = f"{client_name}.conf"
        config_path = os.path.join(DATA_DIR, config_filename)
        with open(config_path, 'w') as f:
            f.write(client_config)
        
        # Генерируем QR код для мобильных устройств
        qr_filename = f"{client_name}_qr.png"
        qr_path = os.path.join(DATA_DIR, qr_filename)
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(client_config)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        
        # Обновляем счетчик пользователей
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        
        return {
            'config': client_config,
            'config_path': config_path,
            'qr_path': qr_path,
            'client_name': client_name,
            'client_ip': client_ip,
            'client_pub_key': client_pub_key,
            'device_type': device_type,
            'instructions': get_wireguard_instructions(device_type)
        }, None
        
    except Exception as e:
        return None, f"Ошибка создания клиента WireGuard: {str(e)}"

def get_wireguard_instructions(device_type: str) -> str:
    """Получение инструкций для WireGuard по типу устройства"""
    base = """🔧 <b>Инструкция по настройке WireGuard:</b>

1. <b>Установите WireGuard</b> на ваше устройство:"""
    
    if device_type == "iphone" or device_type == "ios":
        return base + """
   • App Store: WireGuard от WireGuard LLC

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение WireGuard
   • Нажмите "+" в правом верхнем углу
   • Выберите "Создать из файла или архива"
   • Выберите файл конфигурации
   • Или отсканируйте QR-код

3. <b>Подключитесь</b>:
   • Активируйте переключатель напротив вашего подключения
   • Значок 🔒 означает успешное подключение"""
    
    elif device_type == "android":
        return base + """
   • Google Play: WireGuard от WireGuard LLC

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение WireGuard
   • Нажмите синюю кнопку "+"
   • Выберите "Создать из файла или архива"
   • Выберите файл конфигурации
   • Или отсканируйте QR-код

3. <b>Подключитесь</b>:
   • Нажмите переключатель напротив вашего туннеля
   • Разрешите создание VPN подключения"""
    
    else:  # auto or other
        return base + """
   • Android/iOS: App Store / Google Play
   • Windows/Mac/Linux: https://www.wireguard.com/install/

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение WireGuard
   • Нажмите "+" или "Импорт"
   • Выберите файл конфигурации
   • Или отсканируйте QR-код

3. <b>Подключитесь</b>:
   • Активируйте переключатель
   • Значок 🔒 означает успешное подключение"""

async def create_openvpn_client(server_id: int, client_name: str, username: str, device_type: str):
    """Создание клиента OpenVPN"""
    try:
        # Генерируем клиентский сертификат
        cert_cmds = [
            f"cd /etc/openvpn/easy-rsa && echo '{client_name}' | ./easyrsa gen-req {client_name} nopass",
            f"cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa sign-req client {client_name}",
            f"cd /etc/openvpn/easy-rsa && cat pki/ca.crt pki/issued/{client_name}.crt pki/private/{client_name}.key > /tmp/{client_name}.crt.key"
        ]
        
        for cmd in cert_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
            if not success:
                return None, f"Ошибка генерации сертификата: {stderr}"
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Скачиваем файлы
        download_cmds = [
            f"cd /tmp && cat {client_name}.crt.key",
            "cat /etc/openvpn/ta.key",
            "cat /etc/openvpn/easy-rsa/pki/ca.crt"
        ]
        
        client_cert_key = ""
        ta_key = ""
        ca_cert = ""
        
        for i, cmd in enumerate(download_cmds):
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
            if success:
                if i == 0: client_cert_key = stdout
                elif i == 1: ta_key = stdout
                elif i == 2: ca_cert = stdout
        
        # Создаем конфиг клиента
        client_config = f"""client
dev tun
proto udp
remote {server_ip} 1194
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-CBC
verb 3

<ca>
{ca_cert.strip()}
</ca>

<cert>
{client_cert_key.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0].strip() if '-----BEGIN CERTIFICATE-----' in client_cert_key else ''}
</cert>

<key>
{client_cert_key.split('-----BEGIN PRIVATE KEY-----')[1].split('-----END PRIVATE KEY-----')[0].strip() if '-----BEGIN PRIVATE KEY-----' in client_cert_key else ''}
</key>

<tls-auth>
{ta_key.strip()}
</tls-auth>
key-direction 1
"""
        
        # Сохраняем конфиг
        config_filename = f"{client_name}.ovpn"
        config_path = os.path.join(DATA_DIR, config_filename)
        with open(config_path, 'w') as f:
            f.write(client_config)
        
        # Обновляем счетчик пользователей
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        
        return {
            'config': client_config,
            'config_path': config_path,
            'client_name': client_name,
            'server_ip': server_ip,
            'device_type': device_type,
            'instructions': get_openvpn_instructions(device_type)
        }, None
        
    except Exception as e:
        return None, f"Ошибка создания клиента OpenVPN: {str(e)}"

def get_openvpn_instructions(device_type: str) -> str:
    """Получение инструкций для OpenVPN по типу устройства"""
    base = """🔧 <b>Инструкция по настройке OpenVPN:</b>

1. <b>Установите OpenVPN</b> на ваше устройство:"""
    
    if device_type == "iphone" or device_type == "ios":
        return base + """
   • App Store: OpenVPN Connect

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение OpenVPN Connect
   • Нажмите "+" в правом верхнем углу
   • Выберите "Импорт файла"
   • Выберите файл .ovpn
   • Или отправьте файл себе по почте и откройте его

3. <b>Подключитесь</b>:
   • Нажмите на добавленный профиль
   • Нажмите "Подключиться"
   • При необходимости разрешите создание VPN"""
    
    elif device_type == "android":
        return base + """
   • Google Play: OpenVPN Connect

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение OpenVPN Connect
   • Нажмите значок папки
   • Выберите файл .ovpn
   • Или используйте файловый менеджер для открытия файла

3. <b>Подключитесь</b>:
   • Нажмите на профиль
   • Нажмите "Подключиться"
   • Разрешите создание VPN подключения"""
    
    else:  # auto or other
        return base + """
   • Android/iOS: OpenVPN Connect
   • Windows: OpenVPN GUI
   • Mac: Tunnelblick
   • Linux: openvpn пакет

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение OpenVPN
   • Импортируйте файл .ovpn
   • Для Windows/Mac: скопируйте файл в папку config

3. <b>Подключитесь</b>:
   • Выберите профиль и подключитесь
   • Введите логин/пароль если требуется"""

async def create_ikev2_client(server_id: int, client_name: str, username: str, device_type: str):
    """Создание клиента IPSec/IKEv2"""
    try:
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Генерируем логин/пароль
        password = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=12))
        
        # Добавляем пользователя в конфиг
        add_user_cmd = f'''echo 'vpnuser : EAP "{password}"' >> /etc/ipsec.secrets'''
        
        stdout, stderr, success = await execute_ssh_command(server_id, add_user_cmd, use_sudo=True)
        
        # Перезапускаем сервис
        restart_cmd = "ipsec restart || systemctl restart strongswan || systemctl restart ipsec"
        await execute_ssh_command(server_id, restart_cmd, use_sudo=True)
        
        # Создаем файл с настройками
        config_content = f"""🌐 <b>Настройки IPSec/IKEv2</b>

🔧 <b>Параметры подключения:</b>
• Сервер: {server_ip}
• Тип: IPSec/IKEv2
• Логин: vpnuser
• Пароль: {password}
• Общий ключ: не требуется
• Сертификат: не требуется

📱 <b>Инструкция для {device_type}:</b>

<b>Для iOS/iPhone:</b>
1. Настройки → Основные → VPN
2. Добавить конфигурацию VPN
3. Тип: IKEv2
4. Описание: Любое имя
5. Сервер: {server_ip}
6. Логин: vpnuser
7. Пароль: {password}
8. Готово

<b>Для Android:</b>
1. Настройки → Сеть и интернет → VPN
2. Нажмите "+"
3. Имя: Любое
4. Тип: IPSec Xauth PSK
5. Адрес сервера: {server_ip}
6. Логин: vpnuser
7. Пароль: {password}
8. Сохранить

<b>Для Windows:</b>
1. Параметры → Сеть и Интернет → VPN
2. Добавить VPN подключение
3. Поставщик: Встроенный в Windows
4. Имя: Любое
5. Адрес сервера: {server_ip}
6. Тип VPN: IKEv2
7. Логин/пароль: vpnuser/{password}

⚠️ <b>Важно:</b>
• Сохраните пароль в безопасном месте
• При проблемах перезагрузите устройство
• Для смены пароля обратитесь в поддержку"""
        
        # Сохраняем конфиг
        config_filename = f"{client_name}_ikev2.txt"
        config_path = os.path.join(DATA_DIR, config_filename)
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        # Обновляем счетчик пользователей
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        
        return {
            'config': config_content,
            'config_path': config_path,
            'client_name': client_name,
            'server_ip': server_ip,
            'username': 'vpnuser',
            'password': password,
            'device_type': device_type,
            'instructions': get_ikev2_instructions(device_type, server_ip, password)
        }, None
        
    except Exception as e:
        return None, f"Ошибка создания клиента IKEv2: {str(e)}"

def get_ikev2_instructions(device_type: str, server_ip: str, password: str) -> str:
    """Получение инструкций для IKEv2 по типу устройства"""
    if device_type == "iphone" or device_type == "ios":
        return f"""📱 <b>Инструкция для iPhone/iOS:</b>

1. <b>Настройки</b> → <b>Основные</b> → <b>VPN</b>
2. Нажмите <b>"Добавить конфигурацию VPN..."</b>
3. Заполните поля:
   • Тип: <b>IKEv2</b>
   • Описание: <b>VPN Сервер</b>
   • Сервер: <b>{server_ip}</b>
   • Удаленный ID: <b>{server_ip}</b>
   • Локальный ID: оставить пустым
4. <b>Аутентификация</b>:
   • Имя пользователя: <b>vpnuser</b>
   • Пароль: <b>{password}</b>
5. Нажмите <b>"Готово"</b>
6. Вернитесь и активируйте переключатель VPN"""
    
    elif device_type == "android":
        return f"""📱 <b>Инструкция для Android:</b>

1. <b>Настройки</b> → <b>Сеть и интернет</b> → <b>VPN</b>
2. Нажмите <b>"+"</b> или <b>"Добавить VPN"</b>
3. Заполните поля:
   • Имя: <b>VPN Сервер</b>
   • Тип: <b>IPSec Xauth PSK</b>
   • Адрес сервера: <b>{server_ip}</b>
   • IPSec identifier: <b>{server_ip}</b>
   • IPSec pre-shared key: оставить пустым
4. <b>Аутентификация</b>:
   • Имя пользователя: <b>vpnuser</b>
   • Пароль: <b>{password}</b>
5. Нажмите <b>"Сохранить"</b>
6. Нажмите на созданный профиль и <b>"Подключиться"</b>"""
    
    else:  # auto or other
        return f"""💻 <b>Универсальная инструкция:</b>

<b>Общие параметры:</b>
• Сервер: <b>{server_ip}</b>
• Тип VPN: <b>IPSec/IKEv2</b>
• Логин: <b>vpnuser</b>
• Пароль: <b>{password}</b>

<b>Для разных устройств:</b>

📱 <b>iOS:</b> Настройки → Основные → VPN → Добавить IKEv2
📱 <b>Android:</b> Настройки → Сеть → VPN → Добавить IPSec
💻 <b>Windows:</b> Параметры → Сеть → VPN → Добавить IKEv2
🍎 <b>Mac:</b> Системные настройки → Сеть → + → VPN (IKEv2)

⚠️ <b>Важно:</b> Сохраните пароль! Он не восстанавливается."""

async def send_vpn_config_to_user(user_id: int, vpn_data: dict, message: Message, vpn_type: str):
    """Отправка конфига пользователю"""
    try:
        device_type = vpn_data.get('device_type', 'auto')
        
        if vpn_type == "WireGuard":
            # Отправляем файл конфига
            config_file = FSInputFile(vpn_data['config_path'], filename=f"{vpn_data['client_name']}.conf")
            await bot.send_document(user_id, config_file, caption="📁 Ваш конфигурационный файл WireGuard")
            
            # Отправляем QR код если есть
            if 'qr_path' in vpn_data and os.path.exists(vpn_data['qr_path']):
                qr_file = FSInputFile(vpn_data['qr_path'], filename=f"{vpn_data['client_name']}_qr.png")
                await bot.send_photo(user_id, qr_file, caption="📱 QR-код для быстрой настройки")
            
            # Отправляем инструкцию
            await message.answer(vpn_data['instructions'], parse_mode=ParseMode.HTML)
            
        elif vpn_type == "OpenVPN":
            # Отправляем файл конфига
            config_file = FSInputFile(vpn_data['config_path'], filename=f"{vpn_data['client_name']}.ovpn")
            await bot.send_document(user_id, config_file, caption="📁 Ваш конфигурационный файл OpenVPN")
            
            # Отправляем инструкцию
            await message.answer(vpn_data['instructions'], parse_mode=ParseMode.HTML)
            
        elif vpn_type in ["IPSec/IKEv2", "StrongSwan", "Libreswan"]:
            # Отправляем текстовый файл с настройками
            config_file = FSInputFile(vpn_data['config_path'], filename=f"{vpn_data['client_name']}_settings.txt")
            await bot.send_document(user_id, config_file, caption="📄 Настройки IPSec/IKEv2 подключения")
            
            # Отправляем инструкцию
            instructions = f"""🔧 <b>Ваши данные для подключения:</b>

🌐 <b>Сервер:</b> {vpn_data['server_ip']}
👤 <b>Логин:</b> {vpn_data['username']}
🔑 <b>Пароль:</b> {vpn_data['password']}
📱 <b>Тип:</b> IPSec/IKEv2

{vpn_data['instructions']}

⚠️ <b>Сохраните пароль!</b> Он не восстанавливается."""
            await message.answer(instructions, parse_mode=ParseMode.HTML)
        
        # Сохраняем пути к файлам в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE vpn_users SET config_file_path = ?, qr_code_path = ?, device_type = ? WHERE user_id = ? AND is_active = TRUE", 
                           (vpn_data.get('config_path'), vpn_data.get('qr_path'), device_type, user_id))
            await db.commit()
            
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки конфига: {str(e)}")

async def check_and_clean_expired_subscriptions():
    """Проверка и очистка истекших подписок"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.server_id, v.client_name, v.client_public_key, s.vpn_type 
                FROM vpn_users v 
                JOIN servers s ON v.server_id = s.id
                WHERE v.is_active = TRUE AND v.subscription_end < datetime('now')
            """)
            expired_users = await cursor.fetchall()
            
            for user_id, tg_id, server_id, client_name, client_pub_key, vpn_type in expired_users:
                # Удаляем пользователя с сервера
                if server_id and client_pub_key and vpn_type == "WireGuard":
                    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
                    if ssh_ok:
                        remove_cmd = f"""
                        cd /etc/wireguard
                        wg set wg0 peer {client_pub_key} remove 2>/dev/null || true
                        rm -f {client_name}.private {client_name}.public 2>/dev/null || true
                        wg-quick strip wg0 > wg0.conf.new 2>/dev/null && mv wg0.conf.new wg0.conf 2>/dev/null || true
                        """
                        await execute_ssh_command(server_id, remove_cmd, use_sudo=system_info['has_sudo'])
                
                # Уменьшаем счетчик пользователей
                await db.execute("UPDATE servers SET current_users = current_users - 1 WHERE id = ? AND current_users > 0", (server_id,))
                
                # Деактивируем пользователя
                await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_id,))
                
                # Уведомляем пользователя
                try:
                    await bot.send_message(tg_id, "⚠️ Ваша VPN подписка истекла. Для продления обратитесь в поддержку.")
                except:
                    pass
                
                logger.info(f"Удален истекший VPN пользователь {tg_id}")
            
            await db.commit()
            
    except Exception as e:
        logger.error(f"Ошибка очистки подписок: {e}")

async def get_available_servers(vpn_type: str = 'wireguard') -> List[Dict]:
    """Получение списка доступных серверов"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if vpn_type == "WireGuard":
                condition = "wireguard_configured = TRUE"
            elif vpn_type == "OpenVPN":
                condition = "openvpn_configured = TRUE"
            elif vpn_type in ["IPSec/IKEv2", "StrongSwan", "Libreswan"]:
                condition = "ikev2_configured = TRUE"
            else:
                condition = "is_active = TRUE"
            
            cursor = await db.execute(f"""
                SELECT id, name, current_users, max_users, server_ip, vpn_type 
                FROM servers 
                WHERE is_active = TRUE AND {condition}
                AND current_users < max_users
                ORDER BY current_users ASC
            """)
            servers = await cursor.fetchall()
            
            return [{
                'id': s[0],
                'name': s[1],
                'current_users': s[2],
                'max_users': s[3],
                'server_ip': s[4],
                'vpn_type': s[5],
                'load_percent': (s[2] / s[3] * 100) if s[3] > 0 else 0
            } for s in servers]
    except:
        return []

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [[types.KeyboardButton(text="🔐 Получить VPN")], [types.KeyboardButton(text="📱 Мои услуги")], [types.KeyboardButton(text="🌐 Серверы")], [types.KeyboardButton(text="🆘 Помощь")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    buttons = [[types.KeyboardButton(text="🖥️ Серверы")], [types.KeyboardButton(text="👤 Пользователи")], [types.KeyboardButton(text="💰 Цены")], [types.KeyboardButton(text="🤖 Тест сервера")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def servers_menu():
    buttons = [[types.KeyboardButton(text="📋 Список серверов")], [types.KeyboardButton(text="➕ Добавить сервер")], [types.KeyboardButton(text="🔧 Установить VPN")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_users_menu():
    buttons = [[types.KeyboardButton(text="🎁 Выдать VPN")], [types.KeyboardButton(text="📋 Список пользователей")], [types.KeyboardButton(text="🚫 Отключить VPN")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_type_keyboard():
    buttons = [[types.KeyboardButton(text="WireGuard")], [types.KeyboardButton(text="OpenVPN")], [types.KeyboardButton(text="IPSec/IKEv2")], [types.KeyboardButton(text="StrongSwan")], [types.KeyboardButton(text="Libreswan")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def device_type_keyboard():
    buttons = [[types.KeyboardButton(text="📱 iPhone/iOS")], [types.KeyboardButton(text="🤖 Android")], [types.KeyboardButton(text="💻 Другое")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def install_method_keyboard():
    buttons = [[types.KeyboardButton(text="🚀 Автоустановка")], [types.KeyboardButton(text="🔧 Ручная (Git)")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class AdminAddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_key = State()
    waiting_for_connection = State()
    waiting_for_max_users = State()

class AdminInstallVPNStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_type = State()
    waiting_for_method = State()
    waiting_for_git_repo = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()
    waiting_for_server = State()
    waiting_for_device = State()

class AdminPriceStates(StatesGroup):
    waiting_for_week_price = State()

class AdminTestBotStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_token = State()

class AdminRemoveVPNStates(StatesGroup):
    waiting_for_user = State()

class UserGetVPNStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_server = State()
    waiting_for_device = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать в VPN Hosting!\n\n💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Криптовалюта\n• Банковская карта\n• PayPal\n\n🆘 По вопросам оплаты: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    await message.answer("🖥️ Управление серверами", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список серверов")
async def admin_list_servers(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured, openvpn_configured, ikev2_configured, current_users, max_users, vpn_type FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
    text = "📋 Список серверов:\n\n"
    for server in servers:
        server_id, name, active, wg, ovpn, ike, current, max_users, vpn_type = server
        status = "🟢" if active else "🔴"
        wg_status = "🔐" if wg else "❌"
        ovpn_status = "🅾️" if ovpn else "❌"
        ike_status = "🔑" if ike else "❌"
        load = f"{current}/{max_users}"
        text += f"{status}{wg_status}{ovpn_status}{ike_status} <b>{name}</b> ({vpn_type})\nID: {server_id} | 👥 {load}\n"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_type)
    await message.answer("Выберите тип VPN для этого сервера:", reply_markup=vpn_type_keyboard())

@dp.message(AdminAddServerStates.waiting_for_type)
async def process_server_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    if message.text not in ["WireGuard", "OpenVPN", "IPSec/IKEv2", "StrongSwan", "Libreswan"]:
        await message.answer("Выберите тип из списка:"); return
    await state.update_data(vpn_type=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_max_users)
    await message.answer("Введите максимальное количество пользователей:", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_max_users)
async def process_max_users(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    try:
        max_users = int(message.text)
        if max_users < 1 or max_users > 500:
            await message.answer("Введите число от 1 до 500:"); return
        await state.update_data(max_users=max_users)
        await state.set_state(AdminAddServerStates.waiting_for_key)
        await message.answer("📎 Пришлите файл с SSH ключом (.key, .pem):", reply_markup=back_keyboard())
    except ValueError:
        await message.answer("Введите число:")

@dp.message(AdminAddServerStates.waiting_for_key, F.document)
async def process_ssh_key_file(message: Message, state: FSMContext):
    if not message.document: await message.answer("❌ Отправьте файл с SSH ключом"); return
    file_name = message.document.file_name or ""
    if not file_name.endswith(('.key', '.pem', '.txt')): await message.answer("❌ Файл должен быть .key, .pem или .txt"); return
    await message.answer("📥 Загружаю файл...")
    try:
        file = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file.file_path)
        file_content = downloaded_file.read()
        try: key_text = file_content.decode('utf-8')
        except UnicodeDecodeError: key_text = file_content.decode('utf-8', errors='ignore')
        if '-----BEGIN' not in key_text: key_text = f"-----BEGIN PRIVATE KEY-----\n{key_text}\n-----END PRIVATE KEY-----"
        await state.update_data(ssh_key=key_text)
        await state.set_state(AdminAddServerStates.waiting_for_connection)
        await message.answer("✅ Файл загружен! Введите строку подключения (user@host:port):", reply_markup=back_keyboard())
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    data = await state.get_data()
    if 'ssh_key' not in data: await message.answer("❌ SSH ключ не найден", reply_markup=servers_menu()); await state.clear(); return
    conn_str = message.text.strip()
    if '@' not in conn_str: await message.answer("❌ Формат: user@host или user@host:port"); return
    
    await message.answer("🔍 Проверяю SSH подключение...")
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(None, conn_str, data['ssh_key'])
    
    if not ssh_ok:
        await message.answer(f"❌ SSH недоступен: {ssh_msg}\nСервер не добавлен.", reply_markup=admin_main_menu())
        await state.clear()
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string, vpn_type, max_users) VALUES (?, ?, ?, ?, ?)",
                (data['server_name'], data['ssh_key'], conn_str, data.get('vpn_type', 'wireguard'), data.get('max_users', 50))
            )
            server_id = cursor.lastrowid; await db.commit()
        
        await message.answer(
            f"✅ Сервер '{data['server_name']}' добавлен!\n"
            f"ID: {server_id}\n"
            f"Тип VPN: {data.get('vpn_type', 'wireguard')}\n"
            f"Лимит: {data.get('max_users', 50)} пользователей\n\n"
            f"Теперь установите VPN через меню '🔧 Установить VPN'",
            reply_markup=admin_main_menu()
        )
        await state.clear()
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu()); 
        await state.clear()

@dp.message(F.text == "🔧 Установить VPN")
async def admin_install_vpn_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Нет активных серверов"); return
    text = "🔧 Выберите сервер для установки VPN:\n"
    for server_id, name in servers: text += f"ID: {server_id} - {name}\n"
    text += "\nВведите ID сервера:"
    await state.set_state(AdminInstallVPNStates.waiting_for_server)
    await message.answer(text, reply_markup=back_keyboard())

@dp.message(AdminInstallVPNStates.waiting_for_server)
async def process_install_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    try: server_id = int(message.text)
    except: await message.answer("Введите числовой ID:"); return
    
    # Проверяем существование сервера
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT vpn_type FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: await message.answer("❌ Сервер не найден"); return
            vpn_type = server[0]
    except: await message.answer("❌ Ошибка получения данных"); return
    
    await state.update_data(server_id=server_id, vpn_type=vpn_type)
    await state.set_state(AdminInstallVPNStates.waiting_for_method)
    await message.answer(f"Сервер: ID {server_id}\nТип VPN: {vpn_type}\n\nВыберите метод установки:", reply_markup=install_method_keyboard())

@dp.message(AdminInstallVPNStates.waiting_for_method)
async def process_install_method(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🔧 Установить VPN", reply_markup=servers_menu()); return
    
    data = await state.get_data()
    server_id = data['server_id']
    vpn_type = data['vpn_type']
    
    if message.text == "🚀 Автоустановка":
        success = await setup_vpn_auto(server_id, vpn_type, message)
        if success: await message.answer(f"✅ {vpn_type} успешно установлен!", reply_markup=admin_main_menu())
        else: await message.answer(f"❌ Не удалось установить {vpn_type}", reply_markup=admin_main_menu())
        await state.clear()
    
    elif message.text == "🔧 Ручная (Git)":
        await state.set_state(AdminInstallVPNStates.waiting_for_git_repo)
        await message.answer(f"Введите URL Git репозитория для {vpn_type} (или оставьте пустым для стандартного):", reply_markup=back_keyboard())
    
    else:
        await message.answer("Выберите метод установки:")

@dp.message(AdminInstallVPNStates.waiting_for_git_repo)
async def process_git_repo(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🔧 Установить VPN", reply_markup=servers_menu()); return
    
    data = await state.get_data()
    server_id = data['server_id']
    vpn_type = data['vpn_type']
    
    git_repo = message.text.strip()
    if not git_repo:
        # Стандартные репозитории
        if vpn_type == "WireGuard":
            git_repo = "https://git.zx2c4.com/wireguard-linux-compat"
        elif vpn_type == "OpenVPN":
            git_repo = "https://github.com/OpenVPN/openvpn"
        elif vpn_type in ["IPSec/IKEv2", "StrongSwan"]:
            git_repo = "https://github.com/strongswan/strongswan"
        elif vpn_type == "Libreswan":
            git_repo = "https://github.com/libreswan/libreswan"
        else:
            git_repo = ""
    
    success = await setup_vpn_via_git(server_id, vpn_type, git_repo, message)
    if success: await message.answer(f"✅ {vpn_type} установлен через Git!", reply_markup=admin_main_menu())
    else: await message.answer(f"❌ Не удалось установить {vpn_type}", reply_markup=admin_main_menu())
    await state.clear()

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_gift_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer("Введите username или user_id:", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    username = message.text.replace('@', '').strip(); await state.update_data(username=username)
    await state.set_state(AdminUserStates.waiting_for_period)
    await message.answer("Выберите период:\n1. 3 дня (пробный)\n2. 7 дней\n3. 30 дней\n\nВведите номер:", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_period)
async def process_gift_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    data = await state.get_data(); username = data['username']
    period_map = {"1": 3, "2": 7, "3": 30}
    if message.text not in period_map: await message.answer("Неверный номер. Введите 1, 2 или 3:"); return
    days = period_map[message.text]
    
    await state.update_data(days=days)
    await state.set_state(AdminUserStates.waiting_for_device)
    await message.answer("Выберите тип устройства пользователя:", reply_markup=device_type_keyboard())

@dp.message(AdminUserStates.waiting_for_device)
async def process_gift_device(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    
    device_map = {
        "📱 iPhone/iOS": "iphone",
        "🤖 Android": "android",
        "💻 Другое": "auto"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите тип устройства из списка:"); return
    
    device_type = device_map[message.text]
    data = await state.get_data()
    username = data['username']
    days = data['days']
    
    # Получаем доступные серверы (все типы)
    servers = await get_available_servers()
    if not servers:
        await message.answer("❌ Нет доступных серверов"); await state.clear(); return
    
    await state.update_data(device_type=device_type, servers=servers)
    await state.set_state(AdminUserStates.waiting_for_server)
    
    text = "🖥️ Выберите сервер:\n"
    for server in servers[:10]:
        load_icon = "🟢" if server['load_percent'] < 50 else "🟡" if server['load_percent'] < 80 else "🔴"
        text += f"{load_icon} {server['name']} ({server['vpn_type']}): {server['current_users']}/{server['max_users']} (ID: {server['id']})\n"
    text += "\nВведите ID сервера или 'Авто' для автоматического выбора:"
    
    buttons = [[types.KeyboardButton(text="Авто")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@dp.message(AdminUserStates.waiting_for_server)
async def process_gift_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    
    data = await state.get_data()
    username = data['username']
    days = data['days']
    device_type = data['device_type']
    servers = data['servers']
    
    server_id = None
    server_name = None
    vpn_type = None
    
    if message.text == "Авто":
        # Выбираем наименее загруженный сервер
        servers_sorted = sorted(servers, key=lambda x: x['load_percent'])
        if servers_sorted:
            server_id = servers_sorted[0]['id']
            server_name = servers_sorted[0]['name']
            vpn_type = servers_sorted[0]['vpn_type']
    else:
        try:
            server_id = int(message.text)
            server = next((s for s in servers if s['id'] == server_id), None)
            if not server:
                await message.answer("❌ Неверный ID сервера"); return
            server_name = server['name']
            vpn_type = server['vpn_type']
        except ValueError:
            await message.answer("Введите ID сервера или 'Авто':"); return
    
    if not server_id:
        await message.answer("❌ Не удалось выбрать сервер"); await state.clear(); return
    
    try:
        user_id = 0
        if username.isdigit(): user_id = int(username); username_to_save = f"id_{username}"
        else: username_to_save = username
        
        # Создаем клиента VPN
        vpn_data, error = await create_vpn_client(server_id, user_id, username_to_save, vpn_type, device_type)
        if error:
            await message.answer(f"❌ {error}", reply_markup=admin_main_menu())
            await state.clear()
            return
        
        subscription_end = (datetime.now() + timedelta(days=days)).isoformat()
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, client_public_key, client_ip, config_file_path, qr_code_path, device_type, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username_to_save, server_id, vpn_data['client_name'], vpn_data.get('client_pub_key'), 
                  vpn_data.get('client_ip'), vpn_data.get('config_path'), vpn_data.get('qr_path'), 
                  device_type, subscription_end, days == 3))
            
            # Обновляем счетчик пользователей
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        
        # Отправляем уведомление пользователю
        try:
            if user_id > 0:
                await bot.send_message(user_id, f"🎁 Вам выдан VPN доступ на {days} дней!\n\nСервер: {server_name}\nТип: {vpn_type}\n\nКонфигурация будет отправлена отдельным сообщением.")
                await send_vpn_config_to_user(user_id, vpn_data, message, vpn_type)
        except:
            pass
        
        await message.answer(
            f"✅ VPN выдан!\n👤 @{username}\n📅 {days} дней\n🖥️ {server_name}\n📱 {device_type}\n🔑 {vpn_data['client_name']}",
            reply_markup=admin_main_menu()
        )
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.client_name, v.subscription_end, v.is_active, v.device_type, s.name as server_name 
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                ORDER BY v.subscription_end DESC LIMIT 30
            """)
            users = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not users: await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu()); return
    text = "📋 Список пользователей:\n\n"
    for i, user in enumerate(users[:15], 1):
        user_id, tg_id, username, client_name, sub_end, active, device_type, server_name = user
        status = "🟢" if active else "🔴"; username_display = f"@{username}" if username else f"ID:{tg_id}"
        device_icon = "📱" if device_type == "iphone" else "🤖" if device_type == "android" else "💻"
        if sub_end: 
            sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m')
            days_left = max(0, (datetime.fromisoformat(sub_end) - datetime.now()).days)
            text += f"{i}. {status}{device_icon} {username_display} 📅{sub_date}({days_left}д) 🖥️{server_name or 'N/A'}\n"
        else: text += f"{i}. {status}{device_icon} {username_display} 📅нет подписки\n"
    if len(users) > 15: text += f"\n... и еще {len(users)-15} пользователей"
    text += "\n\nДля отключения введите номер:"
    await state.set_state(AdminRemoveVPNStates.waiting_for_user)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(F.text == "💰 Цены")
async def admin_prices(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear(); prices = await get_vpn_prices()
    text = f"💰 Текущие цены:\n💎 Неделя: {prices['week']['stars']} Stars (${prices['week']['usd']})\n💎 Месяц: {prices['month']['stars']} Stars (${prices['month']['usd']})\n\nВведите новую цену за неделю в Stars:"
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear(); prices = await get_vpn_prices()
    text = f"""🔐 <b>Получить VPN доступ</b>

💳 <b>Способы оплаты:</b>
• Telegram Stars
• Криптовалюта
• Банковская карта
• PayPal

📊 <b>Тарифы:</b>
🎁 <b>3 дня бесплатно</b> - пробный период
💎 <b>7 дней</b> - {prices['week']['stars']} Stars (${prices['week']['usd']})
💎 <b>30 дней</b> - {prices['month']['stars']} Stars (${prices['month']['usd']})

Выберите вариант:"""
    buttons = [[types.KeyboardButton(text="🎁 3 дня (пробный)")], [types.KeyboardButton(text="💎 Неделя")], [types.KeyboardButton(text="💎 Месяц")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message, state: FSMContext):
    # Проверка пробного периода
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        if user and user[0]:
            await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
            return
    
    await state.set_state(UserGetVPNStates.waiting_for_device)
    await state.update_data(period=3, is_trial=True)
    await message.answer("📱 Выберите ваше устройство:", reply_markup=device_type_keyboard())

@dp.message(F.text.in_(["💎 Неделя", "💎 Месяц"]))
async def get_paid_vpn(message: Message, state: FSMContext):
    period = 7 if message.text == "💎 Неделя" else 30
    await state.set_state(UserGetVPNStates.waiting_for_device)
    await state.update_data(period=period, is_trial=False)
    await message.answer("📱 Выберите ваше устройство:", reply_markup=device_type_keyboard())

@dp.message(UserGetVPNStates.waiting_for_device)
async def process_user_device(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await get_vpn_start(message, state)
        return
    
    device_map = {
        "📱 iPhone/iOS": "iphone",
        "🤖 Android": "android",
        "💻 Другое": "auto"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите тип устройства из списка:"); return
    
    device_type = device_map[message.text]
    
    # Получаем доступные серверы
    servers = await get_available_servers()
    if not servers:
        await message.answer("❌ Нет доступных серверов. Попробуйте позже.", reply_markup=user_main_menu())
        await state.clear()
        return
    
    await state.update_data(device_type=device_type, servers=servers)
    await state.set_state(UserGetVPNStates.waiting_for_server)
    
    text = "🖥️ <b>Выберите сервер:</b>\n\n"
    for server in servers[:10]:
        load = server['load_percent']
        load_icon = "🟢" if load < 50 else "🟡" if load < 80 else "🔴"
        load_text = "мало" if load < 50 else "средне" if load < 80 else "много"
        text += f"{load_icon} <b>{server['name']}</b> ({server['vpn_type']})\n"
        text += f"   👥 {server['current_users']}/{server['max_users']} ({load_text})\n"
        text += f"   🆔 ID: {server['id']}\n\n"
    
    data = await state.get_data()
    if data.get('is_trial'):
        text += "Вы выбрали: 🎁 3 дня (пробный)"
    else:
        prices = await get_vpn_prices()
        price = prices['week']['stars'] if data['period'] == 7 else prices['month']['stars']
        text += f"Вы выбрали: 💎 {data['period']} дней ({price} Stars)"
    
    text += f"\nУстройство: {device_type}"
    text += "\n\nВведите ID сервера или 'Авто' для автоматического выбора:"
    
    buttons = [[types.KeyboardButton(text="Авто")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(UserGetVPNStates.waiting_for_server)
async def process_user_server_selection(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.update_data(servers=None)
        await process_user_device(message, state)
        return
    
    data = await state.get_data()
    servers = data.get('servers', [])
    
    if not servers:
        await message.answer("❌ Нет доступных серверов", reply_markup=user_main_menu())
        await state.clear()
        return
    
    server_id = None
    server_name = None
    vpn_type = None
    
    if message.text == "Авто":
        # Выбираем наименее загруженный сервер
        servers_sorted = sorted(servers, key=lambda x: x['load_percent'])
        if servers_sorted:
            server_id = servers_sorted[0]['id']
            server_name = servers_sorted[0]['name']
            vpn_type = servers_sorted[0]['vpn_type']
    else:
        try:
            server_id = int(message.text)
            server = next((s for s in servers if s['id'] == server_id), None)
            if not server:
                await message.answer("❌ Неверный ID сервера"); return
            server_name = server['name']
            vpn_type = server['vpn_type']
        except ValueError:
            await message.answer("Введите ID сервера или 'Авто':"); return
    
    if not server_id:
        await message.answer("❌ Не удалось выбрать сервер", reply_markup=user_main_menu())
        await state.clear()
        return
    
    period = data['period']
    is_trial = data.get('is_trial', False)
    device_type = data['device_type']
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"
    
    # Создаем клиента VPN
    vpn_data, error = await create_vpn_client(server_id, user_id, username, vpn_type, device_type)
    if error:
        await message.answer(f"❌ {error}", reply_markup=user_main_menu())
        await state.clear()
        return
    
    subscription_end = (datetime.now() + timedelta(days=period)).isoformat()
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, client_public_key, client_ip, config_file_path, qr_code_path, device_type, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username, server_id, vpn_data['client_name'], vpn_data.get('client_pub_key'), 
                  vpn_data.get('client_ip'), vpn_data.get('config_path'), vpn_data.get('qr_path'), 
                  device_type, subscription_end, is_trial))
            
            if not is_trial:
                # Записываем платеж
                prices = await get_vpn_prices()
                amount = prices['week']['stars'] if period == 7 else prices['month']['stars']
                await db.execute("""
                    INSERT INTO payments (user_id, amount, currency, payment_method, period_days, status)
                    VALUES (?, ?, 'Stars', 'Telegram Stars', ?, 'completed')
                """, (user_id, amount, period))
            
            await db.commit()
        
        # Отправляем конфиг пользователю
        await send_vpn_config_to_user(user_id, vpn_data, message, vpn_type)
        
        await message.answer(
            f"✅ VPN доступ активирован!\n\n"
            f"📅 Срок действия: {period} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"🔧 Тип: {vpn_type}\n"
            f"📱 Устройство: {device_type}\n"
            f"🔑 Имя клиента: {vpn_data['client_name']}\n\n"
            f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())
    
    await state.clear()

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.subscription_end, v.is_active, v.client_name, v.client_ip, v.device_type, s.name as server_name, s.vpn_type
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                WHERE v.user_id = ? AND v.is_active = TRUE 
                ORDER BY v.subscription_end DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active, client_name, client_ip, device_type, server_name, vpn_type = user
        
        if not is_active:
            await message.answer("❌ Ваша подписка не активна.", reply_markup=user_main_menu())
            return
        
        if sub_end:
            end_date = datetime.fromisoformat(sub_end); now = datetime.now()
            if end_date < now: 
                status = "🔴 Истекла"
                days_left = 0
            else: 
                days_left = (end_date - now).days
                status = f"🟢 Активна ({days_left} дней осталось)"
            
            device_icon = "📱" if device_type == "iphone" else "🤖" if device_type == "android" else "💻"
            
            text = f"📱 <b>Ваша подписка VPN</b>\n\n"
            text += f"<b>Статус:</b> {status}\n"
            text += f"<b>Действует до:</b> {end_date.strftime('%d.%m.%Y %H:%M')}\n"
            if server_name: text += f"<b>Сервер:</b> {server_name}\n"
            text += f"<b>Тип VPN:</b> {vpn_type}\n"
            text += f"<b>Устройство:</b> {device_icon} {device_type}\n"
            if client_name: text += f"<b>Имя клиента:</b> {client_name}\n"
            if client_ip: text += f"<b>Ваш IP:</b> {client_ip}\n"
            
            if days_left < 3 and days_left > 0:
                text += f"\n⚠️ <b>Внимание!</b> Подписка истекает через {days_left} дней.\n"
            
            text += f"\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
            
            # Кнопка для повторной отправки конфига
            buttons = [[types.KeyboardButton(text="📁 Получить конфиг снова")], [types.KeyboardButton(text="◀️ Назад")]]
            await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)
        else:
            await message.answer("📭 Нет информации о подписке", reply_markup=user_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "📁 Получить конфиг снова")
async def resend_config(message: Message):
    """Повторная отправка конфига"""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.config_file_path, v.qr_code_path, v.client_name, v.client_ip, v.device_type, s.vpn_type
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id
                WHERE v.user_id = ? AND v.is_active = TRUE 
                ORDER BY v.subscription_end DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user or not user[0]:
            await message.answer("❌ Конфигурация не найдена", reply_markup=user_main_menu())
            return
        
        config_path, qr_path, client_name, client_ip, device_type, vpn_type = user
        
        vpn_data = {
            'config_path': config_path,
            'qr_path': qr_path if os.path.exists(qr_path) else None,
            'client_name': client_name,
            'client_ip': client_ip,
            'device_type': device_type
        }
        
        # Получаем недостающие данные
        if vpn_type == "WireGuard" and config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                vpn_data['config'] = f.read()
            vpn_data['instructions'] = get_wireguard_instructions(device_type)
        elif vpn_type == "OpenVPN" and config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                vpn_data['config'] = f.read()
            vpn_data['instructions'] = get_openvpn_instructions(device_type)
        
        await send_vpn_config_to_user(user_id, vpn_data, message, vpn_type)
        await message.answer("✅ Конфигурация отправлена!", reply_markup=user_main_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def background_tasks():
    """Фоновые задачи"""
    while True:
        try:
            await check_and_clean_expired_subscriptions()
            await asyncio.sleep(3600)  # Каждый час
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)

# ========== ЗАПУСК ==========
async def main():
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    if not await init_database(): 
        logger.critical("❌ Не удалось инициализировать базу данных!"); return
    me = await bot.get_me()
    print(f"✅ Бот запущен: @{me.username}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Admin Chat ID: {ADMIN_CHAT_ID}")
    
    # Запускаем фоновые задачи
    asyncio.create_task(background_tasks())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except KeyboardInterrupt: 
        logger.info("👋 Бот остановлен")
    except Exception as e: 
        logger.critical(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)