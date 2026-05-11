#!/usr/bin/env python3
"""
StreamFind UK — Setup checker (v2)
Run this once to install dependencies and verify everything works.
"""
import subprocess
import sys
import importlib

REQUIRED = ["requests", "beautifulsoup4"]
IMPORT_NAMES = {"beautifulsoup4": "bs4"}

def check_python():
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 8):
        print(f"  ✗ Python 3.8+ required (you have {v.major}.{v.minor})")
        sys.exit(1)
    print(f"  ✓ Python {v.major}.{v.minor}.{v.micro}")

def install_deps():
    for pkg in REQUIRED:
        import_name = IMPORT_NAMES.get(pkg, pkg)
        try:
            importlib.import_module(import_name)
            print(f"  ✓ {pkg} already installed")
        except ImportError:
            print(f"    Installing {pkg}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
            print(f"  ✓ {pkg} installed")

def test_network():
    import requests
    try:
        r = requests.get("https://www.bbc.co.uk", timeout=5)
        print(f"  ✓ Network OK (BBC responded {r.status_code})")
    except Exception as e:
        print(f"  ✗ Network error: {e}")

if __name__ == "__main__":
    print()
    print("  StreamFind UK — Setup")
    print("  " + "─" * 30)
    check_python()
    install_deps()
    test_network()
    print()
    print("  ✓ Setup complete!")
    print("    Run:  python proxy.py")
    print("    Then open streamfind.html in your browser.")
    print()
