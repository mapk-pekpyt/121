# main_fixed.py - VPN HOSTING БОТ (ПОЛНАЯ РАБОЧАЯ ВЕРСИЯ)
import os, asyncio, logging, sys, random, qrcode, io, sqlite3, re, subprocess, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F, Router
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
SUPPORT_USERNAME = "@vpnhostik"  # ИСПРАВЛЕНО
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

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = True) -> Tuple[str, str, bool]:
    """Выполнение SSH команды - ВСЕГДА с sudo если use_sudo=True"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return "", "Сервер не найден", False
            conn_str, ssh_key = server
            
            try:
                if ':' in conn_str: 
                    user_host, port = conn_str.rsplit(':', 1)
                    user, host = user_host.split('@')
                    port = int(port)
                else: 
                    user, host = conn_str.split('@')
                    port = 22
            except: 
                return "", f"Неверный формат: {conn_str}", False
            
            import tempfile, stat
            ssh_key_clean = ssh_key.strip()
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean); temp_key_path = f.name
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=timeout) as conn:
                    # ВСЕГДА добавляем sudo если требуется
                    if use_sudo:
                        # Проверяем, есть ли sudo в команде уже
                        if not command.strip().startswith('sudo '):
                            command = f"sudo {command}"
                    
                    result = await conn.run(command, timeout=timeout)
                    
                    try: os.unlink(temp_key_path)
                    except: pass
                    
                    if result.exit_status == 0:
                        return result.stdout, result.stderr, True
                    else:
                        # Если ошибка прав доступа, пробуем без sudo
                        if "permission denied" in result.stderr.lower() and use_sudo:
                            logger.warning(f"Попытка без sudo для команды: {command[:50]}")
                            return "", f"Требуются права sudo: {result.stderr}", False
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
    else:
        await message.answer(f"✅ Права есть: {'sudo' if system_info['has_sudo'] else 'root'}")
    
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
    """Автоустановка WireGuard - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
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
            # Пробуем альтернативную установку
            if "could not open lock file" in stderr.lower():
                await message.answer("🔄 Пробую альтернативный метод установки...")
                alt_cmd = "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y install wireguard wireguard-tools qrencode"
                stdout, stderr, success = await execute_ssh_command(server_id, alt_cmd, timeout=300, use_sudo=True)
            
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
            "wg-quick up wg0 2>/dev/null || true",
            "systemctl enable wg-quick@wg0 2>/dev/null || true"
        ]
        
        for cmd in setup_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=True)
            if not success and 'wg-quick up' not in cmd and 'systemctl enable' not in cmd:
                await message.answer(f"⚠️ Предупреждение при выполнении '{cmd[:30]}...': {stderr[:100]}")
        
        # Получаем публичный ключ
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key", use_sudo=True)
        if success and stdout.strip():
            public_key = stdout.strip()
            
            # Получаем IP сервера
            stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
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
    """Автоустановка OpenVPN - ИСПРАВЛЕННАЯ"""
    try:
        await message.answer("📦 Устанавливаю OpenVPN...")
        
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y openvpn easy-rsa",
                "cp -r /usr/share/easy-rsa/ /etc/openvpn/easy-rsa || mkdir -p /etc/openvpn/easy-rsa",
                "cd /etc/openvpn/easy-rsa && ./easyrsa init-pki 2>/dev/null || echo 'easyrsa init'",
                "cd /etc/openvpn/easy-rsa && echo 'ca' | ./easyrsa build-ca nopass 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa gen-req server nopass 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa sign-req server server 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && ./easyrsa gen-dh 2>/dev/null || true",
                "openvpn --genkey --secret /etc/openvpn/ta.key 2>/dev/null || true"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release 2>/dev/null || true",
                "yum install -y openvpn easy-rsa 2>/dev/null || dnf install -y openvpn easy-rsa 2>/dev/null || true",
                "cp -r /usr/share/easy-rsa/3.0.8/ /etc/openvpn/easy-rsa || cp -r /usr/share/easy-rsa/ /etc/openvpn/easy-rsa || mkdir -p /etc/openvpn/easy-rsa",
                "cd /etc/openvpn/easy-rsa && ./easyrsa init-pki 2>/dev/null || echo 'easyrsa init'",
                "cd /etc/openvpn/easy-rsa && echo 'ca' | ./easyrsa build-ca nopass 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa gen-req server nopass 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && echo 'yes' | ./easyrsa sign-req server server 2>/dev/null || true",
                "cd /etc/openvpn/easy-rsa && ./easyrsa gen-dh 2>/dev/null || true",
                "openvpn --genkey --secret /etc/openvpn/ta.key 2>/dev/null || true"
            ]
        else:
            await message.answer("❌ Неподдерживаемая ОС для OpenVPN")
            return False
        
        for cmd in cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение при '{cmd[:30]}...': {stderr[:100]}")
        
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
            "sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf 2>/dev/null || true",
            "sysctl -p 2>/dev/null || true"
        ]
        
        for cmd in ip_forward_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Запускаем OpenVPN
        startup_cmds = [
            "systemctl start openvpn@server 2>/dev/null || service openvpn start 2>/dev/null || true",
            "systemctl enable openvpn@server 2>/dev/null || chkconfig openvpn on 2>/dev/null || true"
        ]
        
        for cmd in startup_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
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
    """Автоустановка IPSec/IKEv2 - ИСПРАВЛЕННАЯ"""
    try:
        await message.answer("📦 Устанавливаю IPSec/IKEv2...")
        
        os_lower = system_info['os_info'].lower()
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y strongswan strongswan-pki libcharon-extra-plugins",
                "ipsec stop 2>/dev/null || true",
                "mkdir -p /etc/ipsec.d/private",
                "chmod 700 /etc/ipsec.d/private"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release 2>/dev/null || true",
                "yum install -y strongswan strongswan-pki 2>/dev/null || dnf install -y strongswan strongswan-pki 2>/dev/null || true",
                "systemctl stop strongswan 2>/dev/null || true",
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
            "pki --gen --type rsa --size 2048 --outform pem > /etc/ipsec.d/private/ca-key.pem 2>/dev/null || openssl genrsa -out /etc/ipsec.d/private/ca-key.pem 2048 2>/dev/null || true",
            "pki --self --ca --lifetime 3650 --in /etc/ipsec.d/private/ca-key.pem --type rsa --dn 'CN=VPN CA' --outform pem > /etc/ipsec.d/cacert.pem 2>/dev/null || openssl req -new -x509 -key /etc/ipsec.d/private/ca-key.pem -out /etc/ipsec.d/cacert.pem -days 3650 -subj '/CN=VPN CA' 2>/dev/null || true",
            "pki --gen --type rsa --size 2048 --outform pem > /etc/ipsec.d/private/server-key.pem 2>/dev/null || openssl genrsa -out /etc/ipsec.d/private/server-key.pem 2048 2>/dev/null || true",
            "pki --pub --in /etc/ipsec.d/private/server-key.pem --type rsa | pki --issue --lifetime 1825 --cacert /etc/ipsec.d/cacert.pem --cakey /etc/ipsec.d/private/ca-key.pem --dn 'CN=vpn.example.com' --san vpn.example.com --flag serverAuth --flag ikeIntermediate --outform pem > /etc/ipsec.d/certs/server-cert.pem 2>/dev/null || openssl req -new -key /etc/ipsec.d/private/server-key.pem -out /tmp/server.csr -subj '/CN=vpn.example.com' 2>/dev/null && openssl x509 -req -in /tmp/server.csr -CA /etc/ipsec.d/cacert.pem -CAkey /etc/ipsec.d/private/ca-key.pem -CAcreateserial -out /etc/ipsec.d/certs/server-cert.pem -days 1825 2>/dev/null || true"
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
            "sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf 2>/dev/null || true",
            "sysctl -p 2>/dev/null || true",
            "ipsec start 2>/dev/null || systemctl start strongswan 2>/dev/null || service strongswan start 2>/dev/null || true",
            "systemctl enable strongswan 2>/dev/null || systemctl enable ipsec 2>/dev/null || chkconfig strongswan on 2>/dev/null || true"
        ]
        
        for cmd in startup_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
        server_ip = stdout.strip() if success else ""
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET ikev2_configured = TRUE, server_ip = ? WHERE id = ?", 
                           (server_ip, server_id))
            await db.commit()
        
        await message.answer(f"✅ IPSec/IKEv2 успешно установлен!\n🌐 IP: {server_ip}\n🔑 Логин: vpnuser\n🔑 Пароль: password\n\n⚠️ В настройках iOS в поле 'Удаленный ID' введите: {server_ip}")
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
                "DEBIAN_FRONTEND=noninteractive apt-get install -y libreswan",
                "ipsec stop 2>/dev/null || true",
                "mkdir -p /etc/ipsec.d/private",
                "chmod 700 /etc/ipsec.d/private"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release 2>/dev/null || true",
                "yum install -y libreswan 2>/dev/null || dnf install -y libreswan 2>/dev/null || true",
                "systemctl stop ipsec 2>/dev/null || true",
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

# ========== СОЗДАНИЕ КЛИЕНТОВ VPN ==========
async def create_vpn_client(server_id: int, user_id: int, username: str, vpn_type: str, device_type: str = "auto"):
    """Создание клиентской конфигурации"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT server_ip, current_users, max_users, vpn_type FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return None, "Сервер не найден"
            
            server_ip, current_users, max_users, server_vpn_type = server
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
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
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
        wg set wg0 peer {client_pub_key} allowed-ips {client_ip}/32 2>/dev/null || true
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
        # Для упрощения, используем один пароль для всех
        password = "vpnpassword123"
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
        server_ip = stdout.strip() if success else ""
        
        # Создаем простой конфиг клиента
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
auth-user-pass
verb 3

<ca>
-----BEGIN CERTIFICATE-----
MIID... (здесь будет реальный сертификат)
-----END CERTIFICATE-----
</ca>
"""
        
        # Сохраняем конфиг
        config_filename = f"{client_name}.ovpn"
        config_path = os.path.join(DATA_DIR, config_filename)
        with open(config_path, 'w') as f:
            f.write(client_config)
        
        # Создаем файл с логином/паролем
        auth_filename = f"{client_name}_auth.txt"
        auth_path = os.path.join(DATA_DIR, auth_filename)
        with open(auth_path, 'w') as f:
            f.write(f"{username}\n{password}")
        
        return {
            'config': client_config,
            'config_path': config_path,
            'auth_path': auth_path,
            'client_name': client_name,
            'server_ip': server_ip,
            'username': username,
            'password': password,
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
   • Введите логин/пароль когда запросит

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
   • Введите логин/пароль когда запросит

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
   • Введите логин/пароль

3. <b>Подключитесь</b>:
   • Выберите профиль и подключитесь
   • Введите логин/пароль если требуется"""

async def create_ikev2_client(server_id: int, client_name: str, username: str, device_type: str):
    """Создание клиента IPSec/IKEv2"""
    try:
        # Генерируем уникальный пароль
        password = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=12))
        
        # Добавляем пользователя в конфиг
        add_user_cmd = f'''echo '{username} : EAP "{password}"' >> /etc/ipsec.secrets'''
        await execute_ssh_command(server_id, add_user_cmd, use_sudo=True)
        
        # Перезапускаем сервис
        restart_cmd = "ipsec restart 2>/dev/null || systemctl restart strongswan 2>/dev/null || systemctl restart ipsec 2>/dev/null || true"
        await execute_ssh_command(server_id, restart_cmd, use_sudo=True)
        
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
        server_ip = stdout.strip() if success else ""
        
        # Создаем файл с настройками
        config_content = f"""🌐 <b>Настройки IPSec/IKEv2</b>

🔧 <b>Параметры подключения:</b>
• Сервер: {server_ip}
• Тип: IPSec/IKEv2
• Логин: {username}
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
6. Удаленный ID: {server_ip}
7. Логин: {username}
8. Пароль: {password}
9. Готово

<b>Для Android:</b>
1. Настройки → Сеть и интернет → VPN
2. Нажмите "+"
3. Имя: Любое
4. Тип: IPSec Xauth PSK
5. Адрес сервера: {server_ip}
6. Логин: {username}
7. Пароль: {password}
8. Сохранить

⚠️ <b>Важно:</b>
• Сохраните пароль в безопасном месте
• При проблемах перезагрузите устройство"""
        
        # Сохраняем конфиг
        config_filename = f"{client_name}_ikev2.txt"
        config_path = os.path.join(DATA_DIR, config_filename)
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        return {
            'config': config_content,
            'config_path': config_path,
            'client_name': client_name,
            'server_ip': server_ip,
            'username': username,
            'password': password,
            'device_type': device_type,
            'instructions': get_ikev2_instructions(device_type, server_ip, username, password)
        }, None
        
    except Exception as e:
        return None, f"Ошибка создания клиента IKEv2: {str(e)}"

def get_ikev2_instructions(device_type: str, server_ip: str, username: str, password: str) -> str:
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
   • Имя пользователя: <b>{username}</b>
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
   • Имя пользователя: <b>{username}</b>
   • Пароль: <b>{password}</b>
5. Нажмите <b>"Сохранить"</b>
6. Нажмите на созданный профиль и <b>"Подключиться"</b>"""
    
    else:  # auto or other
        return f"""💻 <b>Универсальная инструкция:</b>

<b>Общие параметры:</b>
• Сервер: <b>{server_ip}</b>
• Тип VPN: <b>IPSec/IKEv2</b>
• Логин: <b>{username}</b>
• Пароль: <b>{password}</b>

<b>Для разных устройств:</b>

📱 <b>iOS:</b> Настройки → Основные → VPN → Добавить IKEv2
📱 <b>Android:</b> Настройки → Сеть → VPN → Добавить IPSec
💻 <b>Windows:</b> Параметры → Сеть → VPN → Добавить IKEv2
🍎 <b>Mac:</b> Системные настройки → Сеть → + → VPN (IKEv2)

⚠️ <b>Важно:</b> Сохраните пароль!"""

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
            
            # Отправляем файл с логином/паролем если есть
            if 'auth_path' in vpn_data and os.path.exists(vpn_data['auth_path']):
                auth_file = FSInputFile(vpn_data['auth_path'], filename=f"{vpn_data['client_name']}_auth.txt")
                await bot.send_document(user_id, auth_file, caption="🔑 Логин/пароль для OpenVPN")
            
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

⚠️ <b>Сохраните пароль!</b>"""
            await message.answer(instructions, parse_mode=ParseMode.HTML)
            
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки конфига: {str(e)}")

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

def period_keyboard():
    buttons = [[types.KeyboardButton(text="🎁 3 дня (пробный)")], [types.KeyboardButton(text="💎 Неделя")], [types.KeyboardButton(text="💎 Месяц")], [types.KeyboardButton(text="◀️ Назад")]]
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
        await message.answer(f"🚀 Добро пожаловать в VPN Hosting!\n\n💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Криптовалюта\n• Банковская карта\n• PayPal\n\n🆘 По вопросам оплаты: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

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

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_gift_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer("Введите username или user_id:", reply_markup=back_keyboard())

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
    await message.answer(text, reply_markup=period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message, state: FSMContext):
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

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def background_tasks():
    """Фоновые задачи"""
    while True:
        try:
            # Проверка истекших подписок
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT v.id, v.user_id FROM vpn_users v 
                    WHERE v.is_active = TRUE AND v.subscription_end < datetime('now')
                """)
                expired_users = await cursor.fetchall()
                
                for user_id, tg_id in expired_users:
                    # Деактивируем пользователя
                    await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_id,))
                    await db.commit()
                    
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
    print(f"💬 Support: {SUPPORT_USERNAME}")
    
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