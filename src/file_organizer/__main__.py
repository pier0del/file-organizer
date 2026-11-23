"""Permite ejecutar la consola como ``python -m file_organizer [opciones]``."""

import sys

from file_organizer.cli.aplicacion import main

if __name__ == "__main__":
    sys.exit(main())
