"""
Lanzador del CLI para usarlo sin instalar el paquete.
  python main.py                → menú interactivo
  python main.py --carpeta ...  → CLI directo

Si lo instalas (``pip install .``) tienes el comando equivalente ``file-organizer``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from file_organizer.cli.aplicacion import main

if __name__ == "__main__":
    sys.exit(main())
