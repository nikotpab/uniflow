"""
Tests del extractor de tareas (bedrock_extractor.py).
Corre con: python3 -m unittest discover -s tests -v
"""

import json
import unittest
from unittest.mock import MagicMock

from _helpers import install_fake_boto3, load_module

install_fake_boto3()
extractor = load_module("bedrock_extractor", "lambdas/email_scanner/bedrock_extractor.py")


SAMPLE_TASKS = [
    {
        "subject": "Taller de integrales dobles",
        "course": "Cálculo III",
        "due_date": "2026-07-18T23:59:00",
        "description": "Entregar taller",
        "type": "tarea",
        "priority": "alta",
    },
    {
        "subject": "Quiz sobre ondas mecánicas",
        "course": "Física II",
        "due_date": "2026-07-17T14:00:00",
        "description": "Quiz en clase",
        "type": "quiz",
        "priority": "media",
    },
]


class TestParseJsonArray(unittest.TestCase):
    def test_json_plano(self):
        result = extractor._parse_json_array(json.dumps(SAMPLE_TASKS))
        self.assertEqual(len(result), 2)

    def test_json_con_fence_markdown(self):
        raw = "```json\n" + json.dumps(SAMPLE_TASKS) + "\n```"
        result = extractor._parse_json_array(raw)
        self.assertEqual(len(result), 2)

    def test_json_envuelto_en_texto(self):
        raw = "Aquí están las tareas:\n" + json.dumps(SAMPLE_TASKS) + "\nEspero que sirva."
        result = extractor._parse_json_array(raw)
        self.assertEqual(len(result), 2)

    def test_array_vacio(self):
        self.assertEqual(extractor._parse_json_array("[]"), [])

    def test_respuesta_invalida(self):
        self.assertIsNone(extractor._parse_json_array("no hay json aquí"))

    def test_objeto_no_lista(self):
        self.assertIsNone(extractor._parse_json_array('{"subject": "x"}'))


class TestExtractTasksFromEmail(unittest.TestCase):
    EMAIL = {
        "id": "msg-123",
        "subject": "Recordatorio semana 12",
        "sender": "profe@uni.edu",
        "date": "2026-07-10T15:00:00+00:00",
        "body": "cuerpo del email",
    }

    def _mock_bedrock(self, model_text: str):
        response_payload = {
            "output": {"message": {"content": [{"text": model_text}], "role": "assistant"}}
        }
        body = MagicMock()
        body.read.return_value = json.dumps(response_payload).encode()
        client = MagicMock()
        client.invoke_model.return_value = {"body": body}
        extractor.boto3.client = MagicMock(return_value=client)
        return client

    def test_extraccion_y_enriquecimiento(self):
        self._mock_bedrock(json.dumps(SAMPLE_TASKS))
        tasks = extractor.extract_tasks_from_email(self.EMAIL)

        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.assertEqual(task["email_id"], "msg-123")
            self.assertEqual(task["email_subject"], "Recordatorio semana 12")
            self.assertEqual(task["email_date"], self.EMAIL["date"])

    def test_descarta_tareas_sin_subject(self):
        raw = json.dumps([{"course": "X"}, SAMPLE_TASKS[0], "no soy dict"])
        self._mock_bedrock(raw)
        tasks = extractor.extract_tasks_from_email(self.EMAIL)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["subject"], "Taller de integrales dobles")

    def test_respuesta_no_parseable_devuelve_vacio(self):
        self._mock_bedrock("lo siento, no puedo ayudar con eso")
        self.assertEqual(extractor.extract_tasks_from_email(self.EMAIL), [])

    def test_email_sin_tareas(self):
        self._mock_bedrock("[]")
        self.assertEqual(extractor.extract_tasks_from_email(self.EMAIL), [])


if __name__ == "__main__":
    unittest.main()
