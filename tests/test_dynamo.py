"""
Tests del cliente DynamoDB (email_scanner/dynamo_client.py).
Corre con: python3 -m unittest discover -s tests -v
"""

import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from _helpers import install_fake_boto3, load_module

install_fake_boto3()
dynamo = load_module("scanner_dynamo", "lambdas/email_scanner/dynamo_client.py")


class TestDedupPagination(unittest.TestCase):
    """La deduplicación no debe usar Limit (se aplica antes del filtro)."""

    def test_encuentra_match_en_segunda_pagina(self):
        match = {"task_id": "t-2", "email_id": "e-1", "subject": "Taller"}
        page1 = {"Items": [], "LastEvaluatedKey": {"task_id": "t-1"}}
        page2 = {"Items": [match]}

        table = MagicMock()
        table.scan.side_effect = [page1, page2]

        with patch.object(dynamo, "_table", return_value=table):
            result = dynamo.find_task_by_email_and_subject("e-1", "Taller")

        self.assertEqual(result["task_id"], "t-2")
        # Ninguna llamada a scan debe llevar Limit
        for call in table.scan.call_args_list:
            self.assertNotIn("Limit", call.kwargs)

    def test_sin_match_devuelve_none(self):
        table = MagicMock()
        table.scan.side_effect = [{"Items": []}]
        with patch.object(dynamo, "_table", return_value=table):
            self.assertIsNone(dynamo.find_task_by_email_and_subject("e-9", "Nada"))

    def test_email_id_vacio_no_escanea(self):
        table = MagicMock()
        with patch.object(dynamo, "_table", return_value=table):
            self.assertIsNone(dynamo.find_task_by_email_and_subject("", "X"))
        table.scan.assert_not_called()


class TestSaveTask(unittest.TestCase):
    def test_tarea_nueva_devuelve_created_true(self):
        table = MagicMock()
        with patch.object(dynamo, "_table", return_value=table), \
             patch.object(dynamo, "find_task_by_email_and_subject", return_value=None):
            task_id, created = dynamo.save_task({"email_id": "e-1", "subject": "Nueva"})

        self.assertTrue(created)
        table.put_item.assert_called_once()
        item = table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["task_id"], task_id)
        self.assertEqual(item["status"], "pending")

    def test_tarea_existente_devuelve_created_false(self):
        table = MagicMock()
        with patch.object(dynamo, "_table", return_value=table), \
             patch.object(dynamo, "find_task_by_email_and_subject",
                          return_value={"task_id": "ya-existe"}):
            task_id, created = dynamo.save_task({"email_id": "e-1", "subject": "Repetida"})

        self.assertFalse(created)
        self.assertEqual(task_id, "ya-existe")
        table.put_item.assert_not_called()


class TestVentanasDeFecha(unittest.TestCase):
    """
    Caso límite de timezone: 10 jul 2026, 8:00 PM en Bogotá
    (= 11 jul 01:00 UTC). Con UTC el filtro de "hoy" se equivocaba de día.
    """

    FIXED_NOW = datetime(2026, 7, 10, 20, 0, tzinfo=dynamo.LOCAL_TZ)

    TASKS = [
        {"task_id": "hoy", "due_date": "2026-07-10T22:00:00", "status": "pending"},
        {"task_id": "manana", "due_date": "2026-07-11T08:00:00", "status": "pending"},
        {"task_id": "en-6-dias", "due_date": "2026-07-16T23:59:00", "status": "pending"},
        {"task_id": "en-8-dias", "due_date": "2026-07-18T23:59:00", "status": "pending"},
    ]

    def setUp(self):
        fake_datetime = types.SimpleNamespace(now=lambda tz=None: self.FIXED_NOW)
        self._patches = [
            patch.object(dynamo, "datetime", fake_datetime),
            patch.object(dynamo, "get_pending_tasks", return_value=list(self.TASKS)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_hoy_usa_dia_local_no_utc(self):
        ids = [t["task_id"] for t in dynamo.get_tasks_due_today()]
        self.assertEqual(ids, ["hoy"])

    def test_semana_incluye_7_dias_locales(self):
        ids = [t["task_id"] for t in dynamo.get_tasks_due_this_week()]
        self.assertIn("hoy", ids)
        self.assertIn("manana", ids)
        self.assertIn("en-6-dias", ids)
        self.assertNotIn("en-8-dias", ids)


if __name__ == "__main__":
    unittest.main()
