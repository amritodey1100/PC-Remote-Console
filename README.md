# PC Remote Console

A simple, cross-platform remote control server for your PC. Control power, volume, media, mouse, keyboard, and more from your phone or another device via a web UI or HTTP API.

## Features

- Shutdown, restart, sleep, lock, monitor off
- Volume and media key control
- Mouse and keyboard remote
- System stats and screenshot
- Modern mobile-friendly web UI

## Quick Start

1. **Install Python 3.8+**
2. Install dependencies:
   ```sh
   pip install psutil pyautogui Pillow pycaw comtypes qrcode
   ```
3. Start the server:
   ```sh
   python shutdown.py
   ```
4. Open the shown URL (or scan the QR code) on your phone or browser.

## Security

- Change the `SECRET_KEY` in `shutdown.py` before exposing to any network.
- Only run on trusted networks.

## License

MIT
