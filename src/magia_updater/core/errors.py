"""Errores de dominio con mensajes aptos para la GUI y la CLI."""

from __future__ import annotations


class MagiaError(Exception):
    """Error base del actualizador."""


class SdNotFoundError(MagiaError):
    """La ruta indicada no existe o no es una carpeta."""


class SdReadError(MagiaError):
    """No se pudo leer un archivo de la SD."""
