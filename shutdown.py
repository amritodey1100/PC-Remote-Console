import http.server
import socketserver
import json
import os
import sys
import time
import ctypes
import base64
import io
import socket
import threading

# --- CONFIGURATION ---
PORT = 5000
SECRET_KEY = "amritoDey00"  # CHANGE THIS!

# --- DEPENDENCY CHECKS ---
try:
    import psutil
except ImportError:
    psutil = None
    print("[WARN] psutil not installed. System monitoring will be unavailable. Run: pip install psutil")
else:
    try:
        # Prime psutil's non-blocking CPU sampler once at startup.
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Disable fail-safe for remote use
    pyautogui.PAUSE = 0
    pyautogui.MINIMUM_DURATION = 0
    pyautogui.MINIMUM_SLEEP = 0
except ImportError:
    pyautogui = None
    print("[WARN] pyautogui not installed. Media keys & input control will be unavailable. Run: pip install pyautogui")

try:
    from PIL import Image
except ImportError:
    Image = None
    print("[WARN] Pillow not installed. Screenshots will be unavailable. Run: pip install Pillow")

# Volume control via pycaw (lazy-loaded, per-thread COM binding)
_pycaw_available = False
_volume_tls = threading.local()
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _pycaw_available = True
except ImportError:
    print("[WARN] pycaw/comtypes not installed. Volume control will be unavailable. Run: pip install pycaw comtypes")


def _clear_volume():
    _volume_tls.interface = None


def _get_volume(force_refresh=False):
    """Lazy-init volume interface in the current thread COM context."""
    if not (_pycaw_available and sys.platform.startswith("win")):
        return None
    if force_refresh:
        _clear_volume()
    if getattr(_volume_tls, "interface", None) is not None:
        return _volume_tls.interface
    try:
        import comtypes
        comtypes.CoInitialize()
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        _volume_tls.interface = cast(interface, POINTER(IAudioEndpointVolume))
        return _volume_tls.interface
    except Exception as e:
        print(f"[WARN] Failed to init volume: {e}")
        _clear_volume()
        return None

# Map basic power actions to system commands
COMMANDS = {
    "shutdown": "shutdown /s /t 1",
    "restart": "shutdown /r /t 1",
    "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
}


def _build_capabilities():
    """Describe which features are available based on installed dependencies."""
    features = {
        "power_basic": True,
        "power_advanced": sys.platform.startswith("win"),
        "volume": _pycaw_available and sys.platform.startswith("win"),
        "media": bool(pyautogui),
        "stats": bool(psutil),
        "screenshot": bool(pyautogui and Image),
        "mouse": bool(pyautogui),
        "keyboard": bool(pyautogui),
    }

    missing = {}
    if not psutil:
        missing["stats"] = "pip install psutil"
    if not pyautogui:
        missing["media"] = "pip install pyautogui"
        missing["mouse"] = "pip install pyautogui"
        missing["keyboard"] = "pip install pyautogui"
        missing["screenshot"] = "pip install pyautogui Pillow"
    elif not Image:
        missing["screenshot"] = "pip install Pillow"
    if not _pycaw_available:
        missing["volume"] = "pip install pycaw comtypes"

    return {
        "server": "pc-remote-console",
        "version": 1,
        "platform": sys.platform,
        "features": features,
        "actions": {
            "action": sorted(COMMANDS.keys()),
            "power": ["abort", "schedule", "lock", "monitor_off"],
            "media": ["playpause", "next", "prev", "stop", "volumeup", "volumedown"],
            "volume": ["get", "set", "mute", "unmute"],
            "mouse": ["move", "click", "doubleclick", "scroll"],
            "keyboard": ["type", "press", "hotkey"],
        },
        "missing_dependencies": missing,
    }


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        super().server_bind()
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class RemoteControlHandler(http.server.SimpleHTTPRequestHandler):
    """Multi-endpoint remote control HTTP handler."""
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        """Override to clean up log output."""
        print(f"[{self.log_date_time_string()}] {args[0]}")

    # ── CORS ──────────────────────────────────────────────
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────
    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self, data):
        return data.get('token') == SECRET_KEY

    def _check_auth_query(self):
        """Check auth from query string for GET requests."""
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(self.path).query)
        token = query.get('token', [None])[0]
        return token == SECRET_KEY

    # ── Routing ───────────────────────────────────────────
    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path

        if path == '/health':
            self._handle_health()
        elif path == '/capabilities':
            self._handle_capabilities()
        elif path == '/stats':
            self._handle_stats()
        elif path == '/screenshot':
            self._handle_screenshot()
        elif path == '/' or path == '/index.html':
            self._handle_serve_ui()
        else:
            self.send_error(404, "Endpoint not found")

    def _handle_serve_ui(self):
        """Serve the index.html file."""
        try:
            if not os.path.exists('index.html'):
                return self.send_error(404, "index.html not found on server")

            with open('index.html', 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error loading UI: {e}")

    def _handle_health(self):
        """Simple unauthenticated heartbeat used by the mobile UI for connectivity checks."""
        self._send_json({
            "status": "ok",
            "server_time": int(time.time()),
            "platform": sys.platform,
        })

    def _handle_capabilities(self):
        """Return feature availability; requires token."""
        if not self._check_auth_query():
            return self._send_json({"error": "Unauthorized"}, 403)
        self._send_json(_build_capabilities())

    def do_POST(self):
        path = self.path.split('?')[0]
        routes = {
            '/action': self._handle_action,
            '/volume': self._handle_volume,
            '/media': self._handle_media,
            '/power': self._handle_power,
            '/mouse': self._handle_mouse,
            '/keyboard': self._handle_keyboard,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(404, "Endpoint not found")

    # ── 1. Original Power Actions ─────────────────────────
    def _handle_action(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        action = data.get('action')
        command = COMMANDS.get(action)
        if command:
            print(f"[ACTION] Executing: {action} -> {command}")
            os.system(command)
            self._send_json({"status": "success", "message": f"Executed {action}"})
        else:
            self._send_json({"error": "Unknown action"}, 400)

    # ── 2. Volume Control ─────────────────────────────────
    def _handle_volume(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        action = data.get('action')  # set, mute, unmute, get
        for attempt in range(2):
            vol = _get_volume(force_refresh=(attempt == 1))
            if not vol:
                break
            try:
                if action == 'get':
                    level = vol.GetMasterVolumeLevelScalar()
                    muted = vol.GetMute()
                    return self._send_json({"volume": round(level * 100), "muted": bool(muted)})

                elif action == 'set':
                    level = int(data.get('level', 50))
                    level = max(0, min(100, level))
                    vol.SetMasterVolumeLevelScalar(level / 100.0, None)
                    return self._send_json({"status": "success", "volume": level})

                elif action == 'mute':
                    vol.SetMute(1, None)
                    return self._send_json({"status": "success", "muted": True})

                elif action == 'unmute':
                    vol.SetMute(0, None)
                    return self._send_json({"status": "success", "muted": False})

                else:
                    return self._send_json({"error": "Unknown volume action"}, 400)
            except Exception as e:
                print(f"[WARN] Volume action failed (attempt {attempt + 1}): {e}")
                if attempt == 1:
                    return self._send_json({"error": str(e)}, 500)
                _clear_volume()

        return self._send_json({"error": "Volume control not available"}, 503)

    # ── 3. Media Keys ─────────────────────────────────────
    def _handle_media(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        if not pyautogui:
            return self._send_json({"error": "pyautogui not available"}, 503)

        key_map = {
            'playpause': 'playpause',
            'next': 'nexttrack',
            'prev': 'prevtrack',
            'stop': 'stop',
            'volumeup': 'volumeup',
            'volumedown': 'volumedown',
        }
        action = data.get('action')
        key = key_map.get(action)
        if key:
            print(f"[MEDIA] Pressing: {key}")
            pyautogui.press(key)
            self._send_json({"status": "success", "key": key})
        else:
            self._send_json({"error": "Unknown media action"}, 400)

    # ── 4. System Monitoring ──────────────────────────────
    def _handle_stats(self):
        if not self._check_auth_query():
            return self._send_json({"error": "Unauthorized"}, 403)

        if not psutil:
            return self._send_json({"error": "psutil not available"}, 503)

        try:
            boot = psutil.boot_time()
            uptime_secs = int(time.time() - boot)
            hours, remainder = divmod(uptime_secs, 3600)
            minutes, secs = divmod(remainder, 60)

            disk = psutil.disk_usage('/')
            self._send_json({
                "cpu": psutil.cpu_percent(interval=None),
                "ram": psutil.virtual_memory().percent,
                "disk": disk.percent,
                "uptime": f"{hours}h {minutes}m {secs}s",
                "uptime_seconds": uptime_secs,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── 5. Screenshot ─────────────────────────────────────
    def _handle_screenshot(self):
        if not self._check_auth_query():
            return self._send_json({"error": "Unauthorized"}, 403)

        if not pyautogui or not Image:
            return self._send_json({"error": "Screenshot dependencies not available"}, 503)

        try:
            img = pyautogui.screenshot()
            # Resize to keep payload small
            img.thumbnail((800, 450), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=60)
            b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            self._send_json({"image": b64})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── 6. Advanced Power ─────────────────────────────────
    def _handle_power(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        action = data.get('action')
        try:
            if action == 'abort':
                os.system("shutdown /a")
                print("[POWER] Shutdown aborted")
                self._send_json({"status": "success", "message": "Shutdown aborted"})

            elif action == 'schedule':
                minutes = int(data.get('minutes', 30))
                seconds = minutes * 60
                os.system(f"shutdown /s /t {seconds}")
                print(f"[POWER] Scheduled shutdown in {minutes} minutes")
                self._send_json({"status": "success", "message": f"Shutdown in {minutes}min"})

            elif action == 'lock':
                ctypes.windll.user32.LockWorkStation()
                print("[POWER] Screen locked")
                self._send_json({"status": "success", "message": "Screen locked"})

            elif action == 'monitor_off':
                # WM_SYSCOMMAND = 0x0112, SC_MONITORPOWER = 0xF170, 2 = off
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
                print("[POWER] Monitor turned off")
                self._send_json({"status": "success", "message": "Monitor off"})

            else:
                self._send_json({"error": "Unknown power action"}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── 7. Mouse Control ──────────────────────────────────
    def _handle_mouse(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        if not pyautogui:
            return self._send_json({"error": "pyautogui not available"}, 503)

        action = data.get('action')
        try:
            if action == 'move':
                dx = int(data.get('dx', 0))
                dy = int(data.get('dy', 0))
                pyautogui.moveRel(dx, dy, duration=0)
                self._send_json({"status": "success"})

            elif action == 'click':
                button = data.get('button', 'left')
                pyautogui.click(button=button)
                self._send_json({"status": "success"})

            elif action == 'doubleclick':
                pyautogui.doubleClick()
                self._send_json({"status": "success"})

            elif action == 'scroll':
                amount = int(data.get('amount', -3))
                pyautogui.scroll(amount)
                self._send_json({"status": "success"})

            else:
                self._send_json({"error": "Unknown mouse action"}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── 8. Keyboard Control ───────────────────────────────
    def _handle_keyboard(self):
        data = self._read_json()
        if not self._check_auth(data):
            return self._send_json({"error": "Unauthorized"}, 403)

        if not pyautogui:
            return self._send_json({"error": "pyautogui not available"}, 503)

        action = data.get('action')
        try:
            if action == 'type':
                text = data.get('text', '')
                pyautogui.typewrite(text, interval=0.02) if text.isascii() else pyautogui.write(text)
                self._send_json({"status": "success"})

            elif action == 'hotkey':
                keys = data.get('keys', [])
                if keys:
                    pyautogui.hotkey(*keys)
                    self._send_json({"status": "success", "keys": keys})
                else:
                    self._send_json({"error": "No keys provided"}, 400)

            elif action == 'press':
                key = data.get('key', '')
                if key:
                    pyautogui.press(key)
                    self._send_json({"status": "success", "key": key})
                else:
                    self._send_json({"error": "No key provided"}, 400)
            else:
                self._send_json({"error": "Unknown keyboard action"}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def _get_local_ip():
    """Get the local LAN IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    local_ip = _get_local_ip()
    url = f"http://{local_ip}:{PORT}"

    with ThreadingHTTPServer(("", PORT), RemoteControlHandler) as httpd:
        print(f"\n{'='*50}")
        print(f"  PC Remote Console Server")
        print(f"{'='*50}")
        print(f"  Access URL:  {url}")
        print(f"  Port:        {PORT}")
        print(f"{'='*50}")
        print(f"  Endpoints:")
        print(f"    GET  /health     — connectivity check")
        print(f"    GET  /capabilities — feature availability")
        print(f"    POST /action     — shutdown, restart, sleep")
        print(f"    POST /volume     — set, get, mute, unmute")
        print(f"    POST /media      — playpause, next, prev, stop, volumeup, volumedown")
        print(f"    GET  /stats      — CPU, RAM, disk, uptime")
        print(f"    GET  /screenshot — capture screen")
        print(f"    POST /power      — abort, schedule, lock, monitor_off")
        print(f"    POST /mouse      — move, click, scroll")
        print(f"    POST /keyboard   — type, hotkey, press")
        print(f"{'='*50}")
        print(f"  Dependencies:")
        print(f"    psutil:    {'✓' if psutil else '✗ (pip install psutil)'}")
        print(f"    pyautogui: {'✓' if pyautogui else '✗ (pip install pyautogui)'}")
        print(f"    Pillow:    {'✓' if Image else '✗ (pip install Pillow)'}")
        print(f"    pycaw:     {'✓' if _pycaw_available else '✗ (pip install pycaw comtypes)'}")
        print(f"{'='*50}")

        # Print QR Code for easy mobile access
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            print(f"\n  Scan this QR code on your phone:")
            print(f"  (Make sure phone is on the same WiFi)\n")
            qr.print_ascii(invert=True)
        except ImportError:
            print(f"\n  [TIP] Install qrcode for a scannable QR code:")
            print(f"        pip install qrcode")

        print(f"\n  Server is running. Press Ctrl+C to stop.\n")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")

