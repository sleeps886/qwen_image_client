"""Stable paths; never depend on the shell's current directory."""
from pathlib import Path

PROJECT_ROOT=Path(__file__).resolve().parents[3]
BACKEND_ROOT=PROJECT_ROOT/'backend'
APP_ROOT=BACKEND_ROOT/'app'
STATIC_DIR=PROJECT_ROOT/'frontend'
EXAMPLES_DIR=PROJECT_ROOT/'examples'
OUTPUTS_DIR=PROJECT_ROOT/'outputs'
UPLOADS_DIR=PROJECT_ROOT/'work'
LOGS_DIR=PROJECT_ROOT/'logs'
