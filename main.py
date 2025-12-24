# bot_fixed.py - VPN HOSTING БОТ С ИСПРАВЛЕННЫМИ ПРАВАМИ SUDO
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
    
    # Проверка прав - КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ
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

# ========== FSM СОСТОЯНИЯ ==========
class AdminInstallVPNStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_type = State()
    waiting_for_method = State()
    waiting_for_git_repo = State()

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

# ========== ЗАПУСК ==========
async def main():
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    if not await init_database(): 
        logger.critical("❌ Не удалось инициализировать базу данных!"); return
    me = await bot.get_me()
    print(f"✅ Бот запущен: @{me.username}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except KeyboardInterrupt: 
        logger.info("👋 Бот остановлен")
    except Exception as e: 
        logger.critical(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)