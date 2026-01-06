#!/usr/bin/env python3
"""
Quick setup script for Lighter Scalper Bot
"""
import subprocess
import sys
import os
from pathlib import Path


def main():
    print("=" * 60)
    print("Lighter Scalper Bot - Setup")
    print("=" * 60)

    # Check Python version
    if sys.version_info < (3, 8):
        print("Error: Python 3.8+ is required")
        sys.exit(1)

    print(f"Python version: {sys.version}")

    # Install dependencies
    print("\nInstalling dependencies...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-r", "requirements.txt"
    ])

    # Check for .env file
    env_file = Path(".env")
    env_example = Path(".env.example")

    if not env_file.exists():
        if env_example.exists():
            print("\nCreating .env from .env.example...")
            env_file.write_text(env_example.read_text())
            print("Please edit .env with your credentials before running the bot.")
        else:
            print("\nWarning: No .env file found. Create one with your credentials.")
    else:
        print("\n.env file found.")

    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    print("Logs directory ready.")

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Edit .env with your Lighter DEX credentials")
    print("2. Test connection: python run.py test")
    print("3. Dry run (no trading): python run.py dry-run")
    print("4. Live trading: python run.py run")
    print("\nIMPORTANT: Start with small amounts and test thoroughly!")


if __name__ == "__main__":
    main()
