"""Segurança: limite de tentativas, códigos por email e limite diário da IA (sem dependências extras)."""
import hashlib
import json
import os
import secrets
import time
import urllib.request
from collections import defaultdict, deque
from datetime import date

from fastapi import HTTPException, Request

# ---------------------------------------------------------------------------
# Limite de tentativas (em memória: zera se o servidor reiniciar, o que é aceitável)
# ---------------------------------------------------------------------------
_eventos: dict[str, deque] = defaultdict(deque)

def _limpar(chave: str, janela: int) -> deque:
    fila = _eventos[chave]
    agora = time.time()
    while fila and agora - fila[0] > janela:
        fila.popleft()
    return fila

def checar_limite(chave: str, maximo: int, janela: int) -> None:
    """Falha com 429 se já houve `maximo` eventos nos últimos `janela` segundos."""
    fila = _limpar(chave, janela)
    if len(fila) >= maximo:
        minutos = max(1, int((janela - (time.time() - fila[0])) // 60) + 1)
        raise HTTPException(status_code=429, detail=f"Muitas tentativas. Tente de novo em {minutos} min.")

def registrar_evento(chave: str, janela: int) -> None:
    _limpar(chave, janela).append(time.time())

def limpar_eventos(chave: str) -> None:
    _eventos.pop(chave, None)

def ip_do_pedido(pedido: Request) -> str:
    encaminhado = pedido.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return pedido.client.host if pedido.client else "?"

# ---------------------------------------------------------------------------
# Códigos de 6 dígitos
# ---------------------------------------------------------------------------
CODIGO_VALIDADE_MIN = 15
CODIGO_TENTATIVAS = 5

def gerar_codigo() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

def hash_codigo(usuario_id: int, codigo: str) -> str:
    return hashlib.sha256(f"{usuario_id}:{codigo.strip()}".encode()).hexdigest()

# ---------------------------------------------------------------------------
# Envio de email (Brevo, por HTTPS: o Render bloqueia SMTP no plano grátis)
# ---------------------------------------------------------------------------
def email_configurado() -> bool:
    return bool(os.getenv("BREVO_API_KEY") and os.getenv("EMAIL_REMETENTE"))

def enviar_email(para: str, assunto: str, html: str) -> None:
    chave, remetente = os.getenv("BREVO_API_KEY"), os.getenv("EMAIL_REMETENTE")
    if not chave or not remetente:
        raise HTTPException(status_code=503, detail="O envio de emails ainda não está configurado.")
    corpo = json.dumps({"sender": {"name": "Minhas Finanças", "email": remetente},
                        "to": [{"email": para}], "subject": assunto, "htmlContent": html}).encode()
    pedido = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=corpo, method="POST",
                                    headers={"api-key": chave, "content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=10):
            pass
    except Exception as erro:
        print("Falha ao enviar email:", erro)
        raise HTTPException(status_code=502, detail="Não foi possível enviar o email agora. Tente de novo em instantes.")

def html_codigo(titulo: str, codigo: str) -> str:
    return (f"<div style='font-family:Arial,sans-serif;max-width:420px'><h2>{titulo}</h2>"
            f"<p>Seu código é:</p><p style='font-size:32px;letter-spacing:6px;font-weight:bold'>{codigo}</p>"
            f"<p>Vale por {CODIGO_VALIDADE_MIN} minutos. Se não foi você, ignore este email.</p></div>")

# ---------------------------------------------------------------------------
# Limite diário de classificações da IA por usuário
# ---------------------------------------------------------------------------
IA_LIMITE_DIA = 150
_uso_ia: dict[int, tuple[date, int]] = {}

def pode_usar_ia(usuario_id: int) -> bool:
    hoje = date.today()
    dia, n = _uso_ia.get(usuario_id, (hoje, 0))
    if dia != hoje:
        n = 0
    if n >= IA_LIMITE_DIA:
        return False
    _uso_ia[usuario_id] = (hoje, n + 1)
    return True
