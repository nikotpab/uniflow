"""
google_oauth_setup.py
=====================
Script de autorización OAuth2 para Gmail y Google Calendar.

Uso:
    pip install google-auth-oauthlib google-auth boto3
    python3 google_oauth_setup.py

El script abrirá tu navegador para autorizar acceso a Gmail y Calendar,
luego guardará el refresh_token en AWS SSM Parameter Store.
"""

import json
import webbrowser
import urllib.parse
import urllib.request
import http.server
import threading
import boto3
import sys

# ─── Configuración ────────────────────────────────────────────────────────────

AWS_REGION = "us-east-1"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
REDIRECT_URI = "http://localhost:8080/callback"
CALLBACK_PORT = 8080

# ─── Leer credenciales desde SSM ──────────────────────────────────────────────

def get_ssm_param(name: str) -> str:
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    response = ssm.get_parameter(Name=name, WithDecryption=True)
    return response["Parameter"]["Value"]

def save_ssm_param(name: str, value: str) -> None:
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    ssm.put_parameter(
        Name=name,
        Value=value,
        Type="SecureString",
        Overwrite=True,
    )
    print(f"  ✅ Guardado en SSM: {name}")

# ─── Servidor local para capturar el callback OAuth ───────────────────────────

auth_code = None
server_ready = threading.Event()

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;text-align:center;padding:50px">
                <h2>&#x2705; UniFlow autorizado!</h2>
                <p>Puedes cerrar esta ventana y volver a la terminal.</p>
                </body></html>
            """)
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body>Error: {error}</body></html>".encode())

    def log_message(self, format, *args):
        pass  # Silenciar logs del servidor

def start_callback_server():
    server = http.server.HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    server_ready.set()
    server.handle_request()  # Solo una petición

# ─── Flujo OAuth2 ─────────────────────────────────────────────────────────────

def get_authorization_url(client_id: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # Fuerza que devuelva refresh_token
    }
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"
    return base_url + "?" + urllib.parse.urlencode(params)

def exchange_code_for_tokens(code: str, client_id: str, client_secret: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  UniFlow — Configuración OAuth2 de Google")
    print("=" * 60)
    print()

    # Leer credenciales desde SSM
    print("📥 Leyendo credenciales desde SSM...")
    try:
        client_id = get_ssm_param("/uniflow/google/client_id")
        client_secret = get_ssm_param("/uniflow/google/client_secret")
        print("  ✅ Credenciales de Google cargadas")
    except Exception as e:
        print(f"  ❌ Error leyendo SSM: {e}")
        print("  Asegúrate de haber corrido infra/03_create_ssm_params.sh primero")
        sys.exit(1)

    # Iniciar servidor de callback en background
    print("\n🌐 Iniciando servidor local en puerto 8080...")
    server_thread = threading.Thread(target=start_callback_server, daemon=True)
    server_thread.start()
    server_ready.wait(timeout=5)
    print("  ✅ Servidor listo")

    # Generar URL de autorización
    auth_url = get_authorization_url(client_id)

    print("\n🔑 Abriendo navegador para autorizar UniFlow...")
    print(f"   Si no se abre automáticamente, ve a:\n   {auth_url}\n")
    webbrowser.open(auth_url)

    # Esperar el callback
    print("⏳ Esperando autorización... (autoriza en el navegador)")
    server_thread.join(timeout=120)

    if not auth_code:
        print("❌ Tiempo de espera agotado o autorización cancelada")
        sys.exit(1)

    print("  ✅ Código de autorización recibido")

    # Intercambiar código por tokens
    print("\n🔄 Obteniendo tokens de acceso...")
    try:
        tokens = exchange_code_for_tokens(auth_code, client_id, client_secret)
    except Exception as e:
        print(f"  ❌ Error al obtener tokens: {e}")
        sys.exit(1)

    if "refresh_token" not in tokens:
        print("  ❌ No se recibió refresh_token.")
        print("  Si ya autorizaste antes, revoca el acceso en:")
        print("  https://myaccount.google.com/permissions")
        print("  y vuelve a correr este script.")
        sys.exit(1)

    refresh_token = tokens["refresh_token"]
    print(f"  ✅ Refresh token obtenido")

    # Guardar en SSM
    print("\n💾 Guardando en AWS SSM Parameter Store...")
    save_ssm_param("/uniflow/google/refresh_token", refresh_token)

    print("\n" + "=" * 60)
    print("  ✅ Configuración OAuth2 completada!")
    print("=" * 60)
    print("\nUniFlow ya puede acceder a:")
    print("  📧 Gmail (solo lectura) de nicolasbarbosagualteros@gmail.com")
    print("  📅 Google Calendar (crear/editar eventos)")
    print("\nPróximo paso: bash infra/deploy.sh")

if __name__ == "__main__":
    main()
