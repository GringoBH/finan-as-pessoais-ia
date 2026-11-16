"""Gera o par de chaves das notificações push (rode UMA vez e guarde as chaves).

Antes: pip install pywebpush
Depois: python gerar_chaves_push.py
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


chave = ec.generate_private_key(ec.SECP256R1())
privada = b64url(chave.private_numbers().private_value.to_bytes(32, "big"))
publica = b64url(chave.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint))
print("Coloque estas 3 variáveis no Render (Environment):\n")
print("VAPID_PUBLIC_KEY  =", publica)
print("VAPID_PRIVATE_KEY =", privada)
print("VAPID_CONTATO     = mailto:fakelerdo10@gmail.com")
print("\nA PRIVADA é segredo: não mostre a ninguém nem suba para o GitHub.")
