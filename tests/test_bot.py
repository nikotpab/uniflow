"""
Tests del bot de Telegram (telegram_handler.py + lambda_function.py).
Corre con: python3 -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from _helpers import install_fake_boto3, load_module

install_fake_boto3()
# Orden importa: telegram_handler hace `from dynamo_client import ...`
# y `from bedrock_chat import ...`, así que esos aliases deben existir antes.
load_module("dynamo_client", "lambdas/telegram_bot/dynamo_client.py")
load_module("bedrock_chat", "lambdas/telegram_bot/bedrock_chat.py")
handler = load_module("telegram_handler", "lambdas/telegram_bot/telegram_handler.py")
bot_lambda = load_module("bot_lambda", "lambdas/telegram_bot/lambda_function.py")


def _msg(text: str, chat_id: int = 111) -> dict:
    return {"chat": {"id": chat_id}, "text": text}


class TestDispatchComandos(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(handler, "send_message"),
            patch.object(handler, "_get_ssm_optional", return_value=None),
        ]
        self.send = self._patches[0].start()
        self._patches[1].start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _sent_text(self) -> str:
        return self.send.call_args.args[1]

    def test_start_muestra_menu(self):
        handler.process_message(_msg("/start"))
        self.assertIn("UniFlow", self._sent_text())
        self.assertIn("/tareas", self._sent_text())

    def test_hoy_llama_filtro_de_hoy(self):
        with patch.object(handler, "get_tasks_due_today", return_value=[]) as f:
            handler.process_message(_msg("/hoy"))
        f.assert_called_once()
        self.assertIn("hoy", self._sent_text().lower())

    def test_comando_con_sufijo_de_bot(self):
        with patch.object(handler, "get_tasks_due_today", return_value=[]) as f:
            handler.process_message(_msg("/hoy@uniflow_bot"))
        f.assert_called_once()

    def test_comando_desconocido(self):
        handler.process_message(_msg("/noexiste"))
        self.assertIn("no reconocido", self._sent_text())

    def test_texto_libre_va_a_bedrock(self):
        with patch.object(handler, "get_pending_tasks", return_value=[]), \
             patch.object(handler, "generate_response", return_value="respuesta IA") as g:
            handler.process_message(_msg("¿qué tengo pendiente?"))
        g.assert_called_once()
        self.assertEqual(self._sent_text(), "respuesta IA")

    def test_completar_sin_argumentos_pide_formato(self):
        handler.process_message(_msg("/completar"))
        self.assertIn("/completar", self._sent_text())


class TestAllowlist(unittest.TestCase):
    def test_chat_no_autorizado_rechazado(self):
        with patch.object(handler, "send_message") as send, \
             patch.object(handler, "_get_ssm_optional", return_value="12345"):
            handler.process_message(_msg("/start", chat_id=999))
        self.assertIn("privado", send.call_args.args[1])

    def test_chat_autorizado_pasa(self):
        with patch.object(handler, "send_message") as send, \
             patch.object(handler, "_get_ssm_optional", return_value="12345"):
            handler.process_message(_msg("/start", chat_id=12345))
        self.assertIn("UniFlow", send.call_args.args[1])


class TestFormatoTareas(unittest.TestCase):
    def test_escapa_html_en_subject(self):
        task = {
            "task_id": "abc12345",
            "subject": "Ensayo <b>malicioso</b> & raro",
            "course": "Ética",
            "due_date": "2026-07-15T23:59:00",
            "type": "tarea",
        }
        line = handler._format_task(task, 1)
        self.assertIn("&lt;b&gt;malicioso&lt;/b&gt; &amp; raro", line)
        self.assertNotIn("<b>malicioso</b>", line)

    def test_tarea_de_hoy_marca_hoy(self):
        today_local = datetime.now(handler.LOCAL_TZ)
        task = {
            "task_id": "abc12345",
            "subject": "Entrega",
            "course": "General",
            "due_date": today_local.strftime("%Y-%m-%dT23:59:00"),
            "type": "tarea",
        }
        line = handler._format_task(task)
        self.assertIn("HOY", line)

    def test_tarea_vencida_marca_vencida(self):
        ayer = datetime.now(handler.LOCAL_TZ) - timedelta(days=1)
        task = {
            "task_id": "abc12345",
            "subject": "Vieja",
            "course": "General",
            "due_date": ayer.strftime("%Y-%m-%dT08:00:00"),
            "type": "tarea",
        }
        self.assertIn("VENCIDA", handler._format_task(task))


class TestWebhookSecret(unittest.TestCase):
    def test_sin_secreto_configurado_permite(self):
        with patch.object(bot_lambda, "_get_ssm_optional", return_value=None):
            self.assertTrue(bot_lambda._webhook_secret_ok({"headers": {}}))

    def test_secreto_correcto_permite(self):
        event = {"headers": {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"}}
        with patch.object(bot_lambda, "_get_ssm_optional", return_value="s3cr3t"):
            self.assertTrue(bot_lambda._webhook_secret_ok(event))

    def test_header_case_insensitive(self):
        event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"}}
        with patch.object(bot_lambda, "_get_ssm_optional", return_value="s3cr3t"):
            self.assertTrue(bot_lambda._webhook_secret_ok(event))

    def test_secreto_incorrecto_rechaza_con_403(self):
        event = {
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "intruso"},
            "body": '{"message": {"chat": {"id": 1}, "text": "/start"}}',
        }
        with patch.object(bot_lambda, "_get_ssm_optional", return_value="s3cr3t"), \
             patch.object(bot_lambda, "process_message") as pm:
            response = bot_lambda.handler(event, None)
        self.assertEqual(response["statusCode"], 403)
        pm.assert_not_called()

    def test_handler_procesa_mensaje_valido(self):
        event = {
            "headers": {},
            "body": '{"message": {"chat": {"id": 1}, "text": "/start"}}',
        }
        with patch.object(bot_lambda, "_get_ssm_optional", return_value=None), \
             patch.object(bot_lambda, "process_message") as pm:
            response = bot_lambda.handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        pm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
