"""Notificações push (aviso com o app fechado) quando um orçamento chega a 80% ou 100%."""
import json
import os
from datetime import datetime

NIVEIS = (80, 100)


def chave_publica() -> str | None:
    return os.getenv("VAPID_PUBLIC_KEY") or None


def push_configurado() -> bool:
    return bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"))


def enviar_push(repo, usuario_id: int, titulo: str, corpo: str, tag: str) -> None:
    """Envia para todos os aparelhos inscritos do usuário. Nunca levanta erro (é só um aviso extra)."""
    if not push_configurado():
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("pywebpush não instalado: push desativado")
        return
    contato = os.getenv("VAPID_CONTATO") or "mailto:admin@example.com"
    dados = json.dumps({"titulo": titulo, "corpo": corpo, "tag": tag, "url": "./"})
    for ins in repo.listar_inscricoes(usuario_id):
        try:
            webpush(subscription_info={"endpoint": ins.endpoint, "keys": {"p256dh": ins.p256dh, "auth": ins.auth}},
                    data=dados, vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
                    vapid_claims={"sub": contato}, ttl=86400)
        except WebPushException as erro:
            codigo = getattr(getattr(erro, "response", None), "status_code", None)
            if codigo in (404, 410):  # aparelho não existe mais
                repo.apagar_inscricao(ins.endpoint)
            else:
                print("Falha no push:", erro)
        except Exception as erro:
            print("Falha no push:", erro)


def _converter(valor: float, de: str, para: str, taxas: dict) -> float | None:
    if de == para:
        return valor
    if de not in taxas or para not in taxas:
        return None
    return valor / taxas[de] * taxas[para]


def avisar_orcamentos(repo, usuario, taxas: dict) -> None:
    """Confere os orçamentos do usuário no mês atual e envia push quando passa de 80% ou 100%
    (uma vez por nível, por categoria e por mês; se o limite mudar, avisa de novo)."""
    try:
        if not push_configurado() or not repo.listar_inscricoes(usuario.id):
            return
        orcamentos = repo.listar_orcamentos(usuario.id)
        if not orcamentos:
            return
        moeda = usuario.moeda or "EUR"
        mes = datetime.utcnow().strftime("%Y-%m")
        gastos: dict[str, float] = {}
        for d in repo.listar(usuario.id):
            if d.data_hora.strftime("%Y-%m") != mes:
                continue
            v = _converter(d.valor, d.moeda or "EUR", moeda, taxas)
            if v is not None:
                gastos[d.categoria] = gastos.get(d.categoria, 0.0) + v
        for o in orcamentos:
            limite = _converter(o.limite, o.moeda or moeda, moeda, taxas)
            if not limite or limite <= 0:
                continue
            gasto = gastos.get(o.categoria, 0.0)
            nivel = max([n for n in NIVEIS if gasto / limite * 100 >= n] or [0])
            if not nivel:
                continue
            if not repo.subir_aviso_orcamento(usuario.id, o.categoria, mes, round(limite, 2), nivel):
                continue  # esse nível já foi avisado
            if nivel >= 100:
                titulo = f"{o.categoria}: limite ultrapassado!"
                corpo = f"Você gastou {gasto:.2f} {moeda} e o limite do mês era {limite:.2f} {moeda}."
            else:
                titulo = f"{o.categoria}: {int(gasto / limite * 100)}% do limite"
                corpo = f"Você já gastou {gasto:.2f} de {limite:.2f} {moeda} este mês."
            enviar_push(repo, usuario.id, titulo, corpo, f"orc-{o.categoria}")
    except Exception as erro:
        print("Falha ao conferir orçamentos:", erro)
