"""
_helpers.py — utilidades compartidas de los tests
==================================================
Los tests corren sin boto3 instalado (y sin AWS): se instala un boto3
falso en sys.modules antes de cargar los módulos de las Lambdas.

Como las dos Lambdas tienen módulos con el mismo nombre (dynamo_client.py),
los módulos se cargan por ruta explícita con un alias controlado.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent


def install_fake_boto3() -> MagicMock:
    """Instala un boto3 falso (MagicMock) en sys.modules y lo devuelve."""
    fake = MagicMock(name="boto3")
    fake_dynamodb = MagicMock(name="boto3.dynamodb")
    fake_conditions = MagicMock(name="boto3.dynamodb.conditions")
    fake.dynamodb = fake_dynamodb
    fake_dynamodb.conditions = fake_conditions

    sys.modules["boto3"] = fake
    sys.modules["boto3.dynamodb"] = fake_dynamodb
    sys.modules["boto3.dynamodb.conditions"] = fake_conditions
    return fake


def load_module(alias: str, relpath: str):
    """
    Carga un módulo por ruta relativa al root del proyecto y lo registra
    en sys.modules bajo `alias` (necesario para que los imports internos
    entre módulos de una Lambda se resuelvan a la copia correcta).
    """
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module
