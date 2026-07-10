"""
Tests del handler del email scanner (lambda_function.py) y de los
marcadores de emails procesados.

Contexto: el scope de Gmail es solo-lectura, así que el scanner NO marca
emails como leídos (eso devolvía 403). En su lugar registra marcadores en
DynamoDB y salta los emails ya vistos.

Corre con: python3 -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import MagicMock, patch

from _helpers import install_fake_boto3, load_module

install_fake_boto3()
# El lambda del scanner hace `from dynamo_client import ...`: el alias debe
# apuntar a la copia del scanner antes de cargar el handler.
scanner_dynamo = load_module("dynamo_client", "lambdas/email_scanner/dynamo_client.py")
load_module("gmail_client", "lambdas/email_scanner/gmail_client.py")
load_module("bedrock_extractor", "lambdas/email_scanner/bedrock_extractor.py")
load_module("calendar_client", "lambdas/email_scanner/calendar_client.py")
scanner_lambda = load_module("scanner_lambda", "lambdas/email_scanner/lambda_function.py")


EMAIL = {
    "id": "msg-abc",
    "subject": "Recordatorio semana 12",
    "sender": "nikoo.barbosa@gmail.com",
    "date": "2026-07-10T15:00:00+00:00",
    "body": "contenido",
}


class TestSkipEmailsProcesados(unittest.TestCase):
    def _run_handler(self, *, processed: bool, tasks: list):
        with patch.object(scanner_lambda, "_get_ssm", return_value="nikoo.barbosa@gmail.com"), \
             patch.object(scanner_lambda, "get_unread_emails_from_sender", return_value=[dict(EMAIL)]), \
             patch.object(scanner_lambda, "is_email_processed", return_value=processed), \
             patch.object(scanner_lambda, "extract_tasks_from_email", return_value=tasks) as extract, \
             patch.object(scanner_lambda, "save_task", return_value=("t-1", True)) as save, \
             patch.object(scanner_lambda, "create_event_from_task", return_value="ev-1"), \
             patch.object(scanner_lambda, "update_calendar_event_id"), \
             patch.object(scanner_lambda, "save_processed_email_marker") as marker:
            response = scanner_lambda.handler({}, None)
        return response, extract, save, marker

    def test_email_ya_procesado_no_invoca_bedrock(self):
        response, extract, save, marker = self._run_handler(processed=True, tasks=[])
        extract.assert_not_called()
        save.assert_not_called()
        marker.assert_not_called()
        self.assertEqual(response["statusCode"], 200)
        self.assertIn('"emails_skipped": 1', response["body"])

    def test_email_sin_tareas_guarda_marcador(self):
        _, extract, save, marker = self._run_handler(processed=False, tasks=[])
        extract.assert_called_once()
        save.assert_not_called()
        marker.assert_called_once_with("msg-abc", "Recordatorio semana 12")

    def test_email_con_tareas_guarda_marcador_al_final(self):
        task = {"subject": "Taller", "email_id": "msg-abc"}
        _, extract, save, marker = self._run_handler(processed=False, tasks=[task])
        save.assert_called_once()
        marker.assert_called_once_with("msg-abc", "Recordatorio semana 12")


class TestMarcadoresDynamo(unittest.TestCase):
    def test_marcador_invisible_para_tareas_pendientes(self):
        table = MagicMock()
        with patch.object(scanner_dynamo, "_table", return_value=table):
            scanner_dynamo.save_processed_email_marker("msg-9", "Asunto X")

        item = table.put_item.call_args.kwargs["Item"]
        # status != "pending" → nunca aparece en el GSI de tareas pendientes
        self.assertEqual(item["status"], "processed_email")
        self.assertEqual(item["task_id"], "email-marker-msg-9")
        self.assertEqual(item["email_id"], "msg-9")

    def test_is_email_processed_true_si_hay_filas(self):
        table = MagicMock()
        table.scan.return_value = {"Items": [{"task_id": "x"}]}
        with patch.object(scanner_dynamo, "_table", return_value=table):
            self.assertTrue(scanner_dynamo.is_email_processed("msg-1"))

    def test_is_email_processed_false_sin_filas(self):
        table = MagicMock()
        table.scan.return_value = {"Items": []}
        with patch.object(scanner_dynamo, "_table", return_value=table):
            self.assertFalse(scanner_dynamo.is_email_processed("msg-1"))

    def test_is_email_processed_pagina_hasta_encontrar(self):
        table = MagicMock()
        table.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"task_id": "a"}},
            {"Items": [{"task_id": "email-marker-msg-2"}]},
        ]
        with patch.object(scanner_dynamo, "_table", return_value=table):
            self.assertTrue(scanner_dynamo.is_email_processed("msg-2"))

    def test_email_id_vacio_es_false(self):
        table = MagicMock()
        with patch.object(scanner_dynamo, "_table", return_value=table):
            self.assertFalse(scanner_dynamo.is_email_processed(""))
        table.scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
