"""
Lanzador de la interfaz gráfica para usarla sin instalar el paquete.
  python gui.py  →  ventana con 3 pestañas (Organizar, Historial, Cifrado)

Si lo instalas (``pip install .``) tienes el comando equivalente ``file-organizer-gui``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from file_organizer.gui.aplicacion import main

if __name__ == "__main__":
    main()
