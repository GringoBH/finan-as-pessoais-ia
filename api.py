import hashlib
import os
import secrets
import json
import time
import urllib.request
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import RepositorioDespesas
from models import Despesa, Usuario, Recorrente
from recorrencia import FREQUENCIAS, proxima_data
from ia import ClassificadorIA, CATEGORIAS, categoria_valida
from push import chave_publica, avisar_orcamentos
from seguranca import (checar_limite, registrar_evento, limpar_eventos, ip_do_pedido, gerar_codigo, hash_codigo,
                       enviar_email, html_codigo, email_configurado, pode_usar_ia, CODIGO_VALIDADE_MIN, CODIGO_TENTATIVAS)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://gringobh.github.io"],
    allow_methods=["*"],
    allow_headers=["*"],
)

repo = RepositorioDespesas()
ia = ClassificadorIA()

# A conta com este usuário vira admin. Defina ADMIN_LOGIN nas variáveis de ambiente do Render.
ADMIN_LOGIN = (os.getenv("ADMIN_LOGIN") or "").strip().lower()
PRAZOS_LIXEIRA = (1, 7, 30)  # dias; 1 = 24 horas
PREFERENCIAS_PADRAO = {"tema": "auto", "por_pagina": 25, "aba_inicial": "registros",
                       "formato_data": "dmy", "semana_inicio": "seg"}
PREFERENCIAS_VALIDAS = {"tema": ("auto", "claro", "escuro"), "por_pagina": (10, 25, 50, 100),
                        "aba_inicial": ("registros", "resumo"),
                        "formato_data": ("dmy", "mdy", "ymd"), "semana_inicio": ("seg", "dom")}
FOTO_MAX = 200_000  # caracteres do data URL (~150 KB)
# moedas tradicionais com cotação do Banco Central Europeu (Frankfurter); as 4 primeiras aparecem no topo da lista
MOEDAS_FIAT = ("EUR", "BRL", "USD", "GBP",
               "AUD", "BGN", "CAD", "CHF", "CNY", "CZK", "DKK", "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW",
               "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "ZAR")
# moedas digitais: código -> id no CoinGecko
MOEDAS_CRIPTO = {"BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether",
                 "SOL": "solana", "XRP": "ripple", "BNB": "binancecoin"}
MOEDAS = MOEDAS_FIAT + tuple(MOEDAS_CRIPTO)  # aceitas nos gastos


# ----------------------------------------------------------------------
# Modelos de entrada
# ----------------------------------------------------------------------
class DespesaEntrada(BaseModel):
    texto: str
    valor: float
    data: str | None = None  # "AAAA-MM-DD"; se vier vazio, usa o momento atual
    moeda: str | None = None  # se vier vazio, usa a moeda padrão do usuário
    categoria: str | None = None  # se vier vazio, a IA escolhe

class LoginEntrada(BaseModel):
    login: str
    senha: str

class RegistroEntrada(BaseModel):
    login: str
    senha: str
    email: str | None = None
    aceitou_termos: bool = False

class CodigoEntrada(BaseModel):
    codigo: str

class PedirRecuperacao(BaseModel):
    login: str  # usuário ou email

class LixeiraConfig(BaseModel):
    dias: int

class RecorrenteEntrada(BaseModel):
    texto: str
    valor: float
    frequencia: str                 # semanal, mensal ou anual
    moeda: str | None = None
    categoria: str | None = None    # vazio = a IA escolhe
    inicio: str | None = None       # "AAAA-MM-DD": primeira cobrança (vazio = hoje)
    ativo: bool | None = None
    fim: str | None = None          # "AAAA-MM-DD": última cobrança (vazio = sem fim)

class InscricaoEntrada(BaseModel):
    endpoint: str
    keys: dict

class CancelarPush(BaseModel):
    endpoint: str

class OrcamentoEntrada(BaseModel):
    categoria: str
    limite: float | None = None     # vazio ou 0 remove o orçamento

class MoedaConfig(BaseModel):
    moeda: str

class PerfilEntrada(BaseModel):
    nome: str | None = None
    email: str | None = None

class FotoEntrada(BaseModel):
    foto: str | None = None  # data URL (jpeg/png/webp) ou vazio para remover

class SenhaEntrada(BaseModel):
    atual: str
    nova: str

class PreferenciasEntrada(BaseModel):
    tema: str | None = None
    por_pagina: int | None = None
    aba_inicial: str | None = None
    formato_data: str | None = None
    semana_inicio: str | None = None

class RecuperarSenha(BaseModel):
    login: str  # usuário ou email
    codigo: str
    nova: str

class ExcluirConta(BaseModel):
    senha: str


# ----------------------------------------------------------------------
# Funções de apoio
# ----------------------------------------------------------------------
def gerar_hash(senha: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"

def senha_correta(senha: str, guardado: str) -> bool:
    salt = guardado.split("$")[0]
    return secrets.compare_digest(gerar_hash(senha, salt), guardado)

def iso(momento: datetime | None) -> str | None:
    return momento.isoformat() if momento else None

_ultima_limpeza = 0.0

def limpar_lixeira_se_preciso() -> None:
    """Apaga de vez o que venceu. Roda no máximo a cada 10 minutos, quando alguém usa o app."""
    global _ultima_limpeza
    if time.time() - _ultima_limpeza < 600:
        return
    _ultima_limpeza = time.time()
    try:
        repo.purgar_vencidos()
    except Exception as erro:
        print("Falha ao limpar a lixeira:", erro)

def aplicar_admin(usuario: Usuario) -> None:
    if ADMIN_LOGIN and usuario.login == ADMIN_LOGIN and not usuario.is_admin:
        repo.definir_admin(usuario.id)
        usuario.is_admin = True

def usuario_logado(authorization: str = Header(default="")) -> Usuario:
    usuario = repo.usuario_por_token(authorization) if authorization else None
    if not usuario:
        raise HTTPException(status_code=401, detail="Não autenticado")
    agora = datetime.utcnow()
    if usuario.ultimo_acesso is None or (agora - usuario.ultimo_acesso).total_seconds() > 60:
        repo.registrar_acesso(usuario.id)
        usuario.ultimo_acesso = agora
    limpar_lixeira_se_preciso()
    return usuario

def admin_logado(usuario: Usuario = Depends(usuario_logado)) -> Usuario:
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito")
    return usuario

def nova_sessao(usuario: Usuario) -> dict:
    token = secrets.token_hex(32)
    repo.salvar_token(usuario.id, token)
    return {"token": token}

def validar_despesa(entrada: DespesaEntrada) -> None:
    entrada.texto = entrada.texto.strip()
    if not entrada.texto:
        raise HTTPException(status_code=400, detail="A descrição não pode ficar vazia")
    if len(entrada.texto) > 120:
        raise HTTPException(status_code=400, detail="Descrição muito longa (máximo 120 caracteres)")
    if not (0 < entrada.valor < 1_000_000_000):
        raise HTTPException(status_code=400, detail="Valor inválido")

def data_hora_da_entrada(data: str | None) -> datetime:
    if not data:
        return datetime.utcnow()
    try:
        dia = datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida")
    return dia.replace(hour=12)  # meio-dia UTC, para o dia não mudar entre fusos

_cotacoes = {"fiat": {"quando": 0.0, "taxas": None, "data": None},
             "cripto": {"quando": 0.0, "taxas": None}}

def _baixar_json(url: str) -> dict:
    pedido = urllib.request.Request(url, headers={"User-Agent": "minhas-financas/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(pedido, timeout=6) as r:
        return json.load(r)

def _atualizar_fiat() -> None:
    """Moedas comuns (base EUR) via Frankfurter. Guarda 6 horas."""
    outras = ",".join(m for m in MOEDAS_FIAT if m != "EUR")
    for url in (f"https://api.frankfurter.dev/v1/latest?base=EUR&symbols={outras}",
                f"https://api.frankfurter.app/latest?from=EUR&to={outras}"):
        try:
            bruto = _baixar_json(url)
            taxas = {"EUR": 1.0, **{k: float(v) for k, v in bruto["rates"].items()}}
            if all(m in taxas for m in ("EUR", "BRL", "USD", "GBP")):  # as outras são bônus
                _cotacoes["fiat"].update(quando=time.time(), taxas=taxas, data=bruto.get("date"))
                return
        except Exception as erro:
            print("Falha ao buscar cotações:", erro)

def _atualizar_cripto() -> None:
    """Moedas digitais via CoinGecko: quantas unidades valem 1 EUR. Guarda 10 minutos."""
    ids = ",".join(MOEDAS_CRIPTO.values())
    try:
        bruto = _baixar_json(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=eur")
        taxas = {}
        for codigo, id_ in MOEDAS_CRIPTO.items():
            preco = float(bruto[id_]["eur"])
            if preco > 0:
                taxas[codigo] = 1.0 / preco
        if taxas:
            _cotacoes["cripto"].update(quando=time.time(), taxas=taxas)
    except Exception as erro:
        print("Falha ao buscar cotações digitais:", erro)

def buscar_cotacoes() -> dict:
    """Taxas (unidades de cada moeda por 1 EUR). Se uma fonte falhar, usa a última guardada;
    moedas sem nenhuma cotação ficam de fora (o site avisa)."""
    agora = time.time()
    if not _cotacoes["fiat"]["taxas"] or agora - _cotacoes["fiat"]["quando"] > 6 * 3600:
        _atualizar_fiat()
    if not _cotacoes["cripto"]["taxas"] or agora - _cotacoes["cripto"]["quando"] > 600:
        _atualizar_cripto()
    fiat = _cotacoes["fiat"]
    if not fiat["taxas"]:
        raise HTTPException(status_code=503, detail="Cotações indisponíveis agora. Tente de novo em instantes.")
    taxas = dict(fiat["taxas"])
    taxas.update(_cotacoes["cripto"]["taxas"] or {})
    return {"base": "EUR", "data": fiat["data"], "taxas": taxas}

def classificar(usuario_id: int, texto: str) -> str:
    """Pede a categoria à IA, com limite diário por usuário (acima dele, usa "Outros")."""
    return ia.classificar(texto) if pode_usar_ia(usuario_id) else "Outros"

def moeda_valida(moeda: str | None, padrao: str) -> str:
    moeda = (moeda or padrao or "EUR").strip().upper()
    if moeda not in MOEDAS:
        raise HTTPException(status_code=400, detail="Moeda não suportada")
    return moeda

def despesa_para_dict(d: Despesa) -> dict:
    return {
        "id": d.id,
        "descricao": d.descricao,
        "valor": d.valor,
        "moeda": d.moeda or "EUR",
        "categoria": d.categoria,
        "data_hora": d.data_hora.isoformat(),
        "dia_semana": d.data_hora.strftime("%A"),
        "semana_ano": d.data_hora.isocalendar()[1],
        "mes": d.data_hora.month,
        "ano": d.data_hora.year,
    }

# Ao iniciar: garante que a conta do ADMIN_LOGIN (se já existir) é admin
if ADMIN_LOGIN:
    _conta_admin = repo.usuario_por_login(ADMIN_LOGIN)
    if _conta_admin:
        aplicar_admin(_conta_admin)


# ----------------------------------------------------------------------
# Conta: registro, login, dados da conta
# ----------------------------------------------------------------------
def _enviar_codigo(usuario: Usuario, tipo: str) -> None:
    """Gera um código novo (invalida o anterior) e envia para o email da conta."""
    codigo = gerar_codigo()
    titulo = "Confirme seu email" if tipo == "verificar" else "Recuperar senha"
    enviar_email(usuario.email, f"Minhas Finanças: {titulo.lower()}", html_codigo(titulo, codigo))
    repo.guardar_codigo(usuario.id, hash_codigo(usuario.id, codigo), tipo,
                        datetime.utcnow() + timedelta(minutes=CODIGO_VALIDADE_MIN))

def _conferir_codigo(usuario: Usuario, tipo: str, codigo: str) -> None:
    """Confere o código; falha com mensagem genérica se estiver errado, vencido ou esgotado."""
    erro = HTTPException(status_code=400, detail="Código inválido ou vencido")
    if (not usuario.cod_hash or usuario.cod_tipo != tipo or not usuario.cod_expira
            or usuario.cod_expira < datetime.utcnow() or (usuario.cod_tentativas or 0) >= CODIGO_TENTATIVAS):
        raise erro
    if not secrets.compare_digest(hash_codigo(usuario.id, codigo), usuario.cod_hash):
        repo.falha_no_codigo(usuario.id)
        raise erro

@app.post("/registro")
def registro(r: RegistroEntrada, pedido: Request):
    ip = ip_do_pedido(pedido)
    checar_limite(f"registro:{ip}", 10, 3600)
    login = r.login.strip().lower()
    email = (r.email or "").strip().lower() or None
    if len(login) < 3 or " " in login:
        raise HTTPException(status_code=400, detail="Usuário: mínimo 3 caracteres, sem espaços")
    if len(r.senha) < 6:
        raise HTTPException(status_code=400, detail="Senha: mínimo 6 caracteres")
    if not email or "@" not in email or len(email) > 120:
        raise HTTPException(status_code=400, detail="Informe um email válido")
    if not r.aceitou_termos:
        raise HTTPException(status_code=400, detail="É preciso aceitar os Termos e a Política de Privacidade")
    if repo.usuario_por_login(login):
        raise HTTPException(status_code=400, detail="Este usuário já existe")
    if repo.usuario_por_email(email):
        raise HTTPException(status_code=400, detail="Este email já está em uso")
    registrar_evento(f"registro:{ip}", 3600)
    usuario = Usuario(login=login, email=email, senha_hash=gerar_hash(r.senha),
                      is_admin=False, lixeira_dias=7, moeda="EUR", termos_em=datetime.utcnow())
    repo.salvar(usuario)
    aplicar_admin(usuario)
    if email_configurado():
        try:
            _enviar_codigo(usuario, "verificar")  # se falhar, a pessoa pede o código de novo no Perfil
        except HTTPException:
            pass
    return nova_sessao(usuario)

@app.post("/login")
def login(c: LoginEntrada, pedido: Request):
    nome = c.login.strip().lower()
    chave_usuario, chave_ip = f"login-usuario:{nome}", f"login-ip:{ip_do_pedido(pedido)}"
    checar_limite(chave_usuario, 5, 900)   # 5 erros por usuário em 15 min
    checar_limite(chave_ip, 30, 900)       # 30 erros por aparelho em 15 min
    usuario = repo.usuario_por_login(nome)
    if not usuario or not senha_correta(c.senha, usuario.senha_hash):
        registrar_evento(chave_usuario, 900); registrar_evento(chave_ip, 900)
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    limpar_eventos(chave_usuario)
    aplicar_admin(usuario)
    return nova_sessao(usuario)

def preferencias_do_usuario(usuario: Usuario) -> dict:
    prefs = dict(PREFERENCIAS_PADRAO)
    try:
        guardadas = json.loads(usuario.preferencias or "{}")
        for chave, valor in guardadas.items():
            if chave in PREFERENCIAS_VALIDAS and valor in PREFERENCIAS_VALIDAS[chave]:
                prefs[chave] = valor
    except (ValueError, TypeError):
        pass
    return prefs

def _achar_conta(identificador: str):
    ident = identificador.strip().lower()
    return repo.usuario_por_email(ident) if "@" in ident else repo.usuario_por_login(ident)

@app.post("/recuperar-senha/pedir")
def pedir_recuperacao(r: PedirRecuperacao, pedido: Request):
    """Envia um código para o email confirmado da conta. A resposta é sempre a mesma (não revela se a conta existe)."""
    ip = ip_do_pedido(pedido)
    checar_limite(f"recuperar-ip:{ip}", 10, 3600)
    registrar_evento(f"recuperar-ip:{ip}", 3600)
    if not email_configurado():
        raise HTTPException(status_code=503, detail="A recuperação por email ainda não está ativa. Fale com o suporte.")
    usuario = _achar_conta(r.login)
    if usuario and usuario.email and usuario.email_verificado:
        checar_limite(f"recuperar-conta:{usuario.id}", 3, 3600)  # no máximo 3 códigos por hora por conta
        registrar_evento(f"recuperar-conta:{usuario.id}", 3600)
        _enviar_codigo(usuario, "recuperar")
    return {"ok": True}

@app.post("/recuperar-senha")
def recuperar_senha(r: RecuperarSenha, pedido: Request):
    ip = ip_do_pedido(pedido)
    checar_limite(f"recuperar-codigo:{ip}", 15, 3600)
    registrar_evento(f"recuperar-codigo:{ip}", 3600)
    if len(r.nova) < 6:
        raise HTTPException(status_code=400, detail="Senha nova: mínimo 6 caracteres")
    usuario = _achar_conta(r.login)
    if not usuario:
        raise HTTPException(status_code=400, detail="Código inválido ou vencido")
    _conferir_codigo(usuario, "recuperar", r.codigo)
    repo.definir_senha(usuario.id, gerar_hash(r.nova), secrets.token_hex(32))  # encerra as sessões antigas
    repo.limpar_codigo(usuario.id)
    return {"ok": True}

@app.post("/me/email/codigo")
def enviar_codigo_email(usuario: Usuario = Depends(usuario_logado)):
    if not usuario.email:
        raise HTTPException(status_code=400, detail="Cadastre um email no Perfil primeiro")
    if usuario.email_verificado:
        return {"ok": True, "ja_confirmado": True}
    checar_limite(f"verificar-conta:{usuario.id}", 5, 3600)
    registrar_evento(f"verificar-conta:{usuario.id}", 3600)
    _enviar_codigo(usuario, "verificar")
    return {"ok": True}

@app.post("/me/email/confirmar")
def confirmar_email(c: CodigoEntrada, usuario: Usuario = Depends(usuario_logado)):
    atual = repo.usuario_por_id(usuario.id)
    _conferir_codigo(atual, "verificar", c.codigo)
    repo.marcar_email_verificado(usuario.id)
    return {"ok": True}

@app.get("/me")
def meus_dados(usuario: Usuario = Depends(usuario_logado)):
    return {
        "login": usuario.login,
        "nome": usuario.nome,
        "foto": usuario.foto,
        "preferencias": preferencias_do_usuario(usuario),
        "email": usuario.email,
        "email_verificado": bool(usuario.email_verificado),
        "email_ativo": email_configurado(),
        "is_admin": bool(usuario.is_admin),
        "lixeira_dias": usuario.lixeira_dias,
        "moeda": usuario.moeda or "EUR",
        "moedas": list(MOEDAS),
        "moedas_padrao": list(MOEDAS_FIAT),
        "categorias": CATEGORIAS,
    }

@app.put("/me/moeda")
def configurar_moeda(c: MoedaConfig, usuario: Usuario = Depends(usuario_logado)):
    moeda = moeda_valida(c.moeda, "EUR")
    if moeda not in MOEDAS_FIAT:
        raise HTTPException(status_code=400, detail="Escolha uma moeda comum como padrão")
    repo.definir_moeda(usuario.id, moeda)
    return {"ok": True, "moeda": moeda}

@app.put("/me/perfil")
def atualizar_perfil(p: PerfilEntrada, usuario: Usuario = Depends(usuario_logado)):
    nome = (p.nome or "").strip() or None
    email = (p.email or "").strip().lower() or None
    if nome and len(nome) > 40:
        raise HTTPException(status_code=400, detail="Nome muito longo (máximo 40 caracteres)")
    if email and ("@" not in email or len(email) > 120):
        raise HTTPException(status_code=400, detail="Email inválido")
    if email and email != usuario.email:
        outro = repo.usuario_por_email(email)
        if outro and outro.id != usuario.id:
            raise HTTPException(status_code=400, detail="Este email já está em uso")
    repo.atualizar_perfil(usuario.id, nome, email)
    return {"ok": True, "nome": nome, "email": email}

@app.put("/me/foto")
def atualizar_foto(f: FotoEntrada, usuario: Usuario = Depends(usuario_logado)):
    foto = f.foto or None
    if foto:
        if not foto.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
            raise HTTPException(status_code=400, detail="Formato de imagem inválido")
        if len(foto) > FOTO_MAX:
            raise HTTPException(status_code=400, detail="Imagem muito grande")
    repo.definir_foto(usuario.id, foto)
    return {"ok": True}

@app.put("/me/preferencias")
def atualizar_preferencias(p: PreferenciasEntrada, usuario: Usuario = Depends(usuario_logado)):
    prefs = preferencias_do_usuario(usuario)
    for chave in PREFERENCIAS_VALIDAS:
        valor = getattr(p, chave)
        if valor is None:
            continue
        if valor not in PREFERENCIAS_VALIDAS[chave]:
            raise HTTPException(status_code=400, detail="Preferência inválida")
        prefs[chave] = valor
    repo.definir_preferencias(usuario.id, json.dumps(prefs))
    return {"ok": True, "preferencias": prefs}

@app.post("/me/senha")
def trocar_senha(s: SenhaEntrada, usuario: Usuario = Depends(usuario_logado)):
    if not senha_correta(s.atual, usuario.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if len(s.nova) < 6:
        raise HTTPException(status_code=400, detail="Senha nova: mínimo 6 caracteres")
    token = secrets.token_hex(32)
    repo.definir_senha(usuario.id, gerar_hash(s.nova), token)
    return {"ok": True, "token": token}

@app.get("/cotacoes")
def cotacoes(usuario: Usuario = Depends(usuario_logado)):
    return buscar_cotacoes()

@app.put("/me/lixeira")
def configurar_lixeira(c: LixeiraConfig, usuario: Usuario = Depends(usuario_logado)):
    if c.dias not in PRAZOS_LIXEIRA:
        raise HTTPException(status_code=400, detail="Prazo inválido")
    repo.definir_lixeira_dias(usuario.id, c.dias)
    return {"ok": True, "lixeira_dias": c.dias}

@app.post("/me/excluir")
def excluir_conta(c: ExcluirConta, usuario: Usuario = Depends(usuario_logado)):
    if not senha_correta(c.senha, usuario.senha_hash):
        raise HTTPException(status_code=400, detail="Senha incorreta")
    repo.apagar_conta(usuario.id)
    return {"ok": True}


# ----------------------------------------------------------------------
# Gastos
# ----------------------------------------------------------------------
@app.post("/despesas")
def criar_despesa(entrada: DespesaEntrada, tarefas: BackgroundTasks, usuario: Usuario = Depends(usuario_logado)):
    validar_despesa(entrada)
    data_hora = data_hora_da_entrada(entrada.data)
    categoria = categoria_valida(entrada.categoria) or classificar(usuario.id, entrada.texto)
    moeda = moeda_valida(entrada.moeda, usuario.moeda)
    repo.salvar(Despesa(usuario_id=usuario.id, descricao=entrada.texto, valor=entrada.valor,
                        categoria=categoria, moeda=moeda, data_hora=data_hora))
    tarefas.add_task(conferir_orcamentos, usuario)
    return {"descricao": entrada.texto, "valor": entrada.valor, "moeda": moeda, "categoria": categoria}

def conferir_orcamentos(usuario: Usuario) -> None:
    """Depois de salvar um gasto: avisa por push se algum orçamento chegou a 80% ou 100%."""
    try:
        avisar_orcamentos(repo, usuario, buscar_cotacoes()["taxas"])
    except Exception as erro:
        print("Falha ao conferir orçamentos:", erro)

@app.get("/push/chave")
def chave_push(usuario: Usuario = Depends(usuario_logado)):
    return {"chave": chave_publica()}

@app.post("/push/inscrever")
def inscrever_push(i: InscricaoEntrada, usuario: Usuario = Depends(usuario_logado)):
    p256dh, auth = i.keys.get("p256dh"), i.keys.get("auth")
    if not (i.endpoint.startswith("https://") and len(i.endpoint) < 1000 and isinstance(p256dh, str) and isinstance(auth, str)):
        raise HTTPException(status_code=400, detail="Inscrição inválida")
    repo.salvar_inscricao(usuario.id, i.endpoint, p256dh, auth)
    return {"ok": True}

@app.post("/push/cancelar")
def cancelar_push(c: CancelarPush, usuario: Usuario = Depends(usuario_logado)):
    repo.apagar_inscricao(c.endpoint, usuario.id)
    return {"ok": True}

@app.get("/despesas")
def listar_despesas(usuario: Usuario = Depends(usuario_logado)):
    repo.lancar_recorrentes(usuario.id, datetime.utcnow().date())  # gastos fixos que venceram
    return [despesa_para_dict(d) for d in repo.listar(usuario.id)]

@app.put("/despesas/{despesa_id}")
def editar_despesa(despesa_id: int, entrada: DespesaEntrada, tarefas: BackgroundTasks, usuario: Usuario = Depends(usuario_logado)):
    validar_despesa(entrada)
    atual = repo.buscar_despesa(despesa_id, usuario.id)
    if not atual:
        raise HTTPException(status_code=404, detail="Despesa não encontrada")
    # só pede nova categoria à IA se a descrição mudou
    escolhida = categoria_valida(entrada.categoria)
    if escolhida:
        categoria = escolhida  # o usuário escolheu a categoria
    else:
        categoria = atual.categoria if entrada.texto == atual.descricao else classificar(usuario.id, entrada.texto)
    nova_data = data_hora_da_entrada(entrada.data) if entrada.data else None
    moeda = moeda_valida(entrada.moeda, atual.moeda)
    repo.atualizar_despesa(despesa_id, usuario.id, entrada.texto, entrada.valor, categoria, nova_data, moeda)
    tarefas.add_task(conferir_orcamentos, usuario)
    return {"ok": True, "categoria": categoria}

@app.delete("/despesas/{despesa_id}")
def mover_para_lixeira(despesa_id: int, usuario: Usuario = Depends(usuario_logado)):
    """Apagar = mover para a lixeira, pelo prazo que o usuário escolheu."""
    expira = repo.mover_para_lixeira(despesa_id, usuario.id, usuario.lixeira_dias)
    if expira is None:
        raise HTTPException(status_code=404, detail="Despesa não encontrada")
    return {"ok": True, "expira_em": iso(expira)}


# ----------------------------------------------------------------------
# Gastos fixos (recorrentes)
# ----------------------------------------------------------------------
def recorrente_para_dict(r: Recorrente) -> dict:
    return {"id": r.id, "descricao": r.descricao, "valor": r.valor, "moeda": r.moeda,
            "categoria": r.categoria, "frequencia": r.frequencia,
            "proxima": r.proxima.isoformat(), "ativo": bool(r.ativo),
            "fim": r.fim.isoformat() if r.fim else None,
            "encerrado": bool(r.fim and r.proxima > r.fim)}

def data_do_dia(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida")

def validar_recorrente(e: RecorrenteEntrada) -> None:
    validar_despesa(DespesaEntrada(texto=e.texto, valor=e.valor))
    e.texto = e.texto.strip()
    if e.frequencia not in FREQUENCIAS:
        raise HTTPException(status_code=400, detail="Frequência inválida")

def validar_fim(fim: date | None, proxima: date) -> None:
    if fim and fim < proxima:
        raise HTTPException(status_code=400, detail="A data de término não pode ser antes da próxima cobrança")
    if fim and fim > proxima + timedelta(days=366 * 30):
        raise HTTPException(status_code=400, detail="Data de término muito distante")

@app.get("/recorrentes")
def listar_recorrentes(usuario: Usuario = Depends(usuario_logado)):
    return [recorrente_para_dict(r) for r in repo.listar_recorrentes(usuario.id)]

@app.post("/recorrentes")
def criar_recorrente(e: RecorrenteEntrada, usuario: Usuario = Depends(usuario_logado)):
    validar_recorrente(e)
    hoje = datetime.utcnow().date()
    inicio = data_do_dia(e.inicio) or hoje
    if inicio < hoje - timedelta(days=366):
        raise HTTPException(status_code=400, detail="A primeira cobrança não pode ser de mais de 1 ano atrás")
    if inicio > hoje + timedelta(days=366 * 2):
        raise HTTPException(status_code=400, detail="Data de início muito distante")
    fim = data_do_dia(e.fim)
    validar_fim(fim, inicio)
    categoria = categoria_valida(e.categoria) or classificar(usuario.id, e.texto)
    repo.salvar(Recorrente(usuario_id=usuario.id, descricao=e.texto, valor=e.valor,
                           moeda=moeda_valida(e.moeda, usuario.moeda), categoria=categoria,
                           frequencia=e.frequencia, dia_base=inicio.day, proxima=inicio, fim=fim, ativo=True))
    repo.lancar_recorrentes(usuario.id, hoje)  # se começa hoje (ou antes), lança já
    return {"ok": True}

@app.put("/recorrentes/{rec_id}")
def editar_recorrente(rec_id: int, e: RecorrenteEntrada, usuario: Usuario = Depends(usuario_logado)):
    validar_recorrente(e)
    atual = repo.buscar_recorrente(rec_id, usuario.id)
    if not atual:
        raise HTTPException(status_code=404, detail="Gasto fixo não encontrado")
    campos = {"descricao": e.texto, "valor": e.valor, "frequencia": e.frequencia,
              "moeda": moeda_valida(e.moeda, atual.moeda),
              "categoria": categoria_valida(e.categoria)
              or (atual.categoria if e.texto == atual.descricao else classificar(usuario.id, e.texto))}
    if e.ativo is not None:
        campos["ativo"] = e.ativo
    inicio = data_do_dia(e.inicio)
    if inicio:  # nova data da próxima cobrança
        campos["proxima"] = inicio
        campos["dia_base"] = inicio.day
    if e.fim is not None:  # "" remove o término
        campos["fim"] = data_do_dia(e.fim)
    proxima = campos.get("proxima", atual.proxima)
    fim = campos["fim"] if "fim" in campos else atual.fim
    if "fim" in campos and campos["fim"] != atual.fim or "proxima" in campos:
        validar_fim(fim, proxima)
    if campos.get("ativo") and fim and proxima > fim:
        raise HTTPException(status_code=400, detail="Este gasto fixo já terminou. Mude a data de término para retomar.")
    repo.atualizar_recorrente(rec_id, usuario.id, **campos)
    repo.lancar_recorrentes(usuario.id, datetime.utcnow().date())
    return {"ok": True}

@app.delete("/recorrentes/{rec_id}")
def apagar_recorrente(rec_id: int, usuario: Usuario = Depends(usuario_logado)):
    """Para de lançar. Os gastos já lançados continuam nos registros."""
    if not repo.apagar_recorrente(rec_id, usuario.id):
        raise HTTPException(status_code=404, detail="Gasto fixo não encontrado")
    return {"ok": True}


# ----------------------------------------------------------------------
# Orçamentos por categoria
# ----------------------------------------------------------------------
@app.get("/orcamentos")
def listar_orcamentos(usuario: Usuario = Depends(usuario_logado)):
    return [{"categoria": o.categoria, "limite": o.limite, "moeda": o.moeda}
            for o in repo.listar_orcamentos(usuario.id)]

@app.put("/orcamentos")
def definir_orcamento(o: OrcamentoEntrada, usuario: Usuario = Depends(usuario_logado)):
    categoria = o.categoria.strip()
    if not categoria or len(categoria) > 40:
        raise HTTPException(status_code=400, detail="Categoria inválida")
    if o.limite is not None and not (0 <= o.limite < 1_000_000_000):
        raise HTTPException(status_code=400, detail="Limite inválido")
    repo.definir_orcamento(usuario.id, categoria, o.limite, usuario.moeda or "EUR")
    return {"ok": True}


# ----------------------------------------------------------------------
# Lixeira
# ----------------------------------------------------------------------
@app.get("/lixeira")
def ver_lixeira(usuario: Usuario = Depends(usuario_logado)):
    return [
        {**despesa_para_dict(d), "apagado_em": iso(d.apagado_em), "expira_em": iso(d.expira_em)}
        for d in repo.listar_lixeira(usuario.id)
    ]

@app.post("/lixeira/{despesa_id}/restaurar")
def restaurar_despesa(despesa_id: int, usuario: Usuario = Depends(usuario_logado)):
    if not repo.restaurar(despesa_id, usuario.id):
        raise HTTPException(status_code=404, detail="Este gasto já não está na lixeira")
    return {"ok": True}

@app.delete("/lixeira/{despesa_id}")
def apagar_de_vez(despesa_id: int, usuario: Usuario = Depends(usuario_logado)):
    if not repo.apagar_de_vez(despesa_id, usuario.id):
        raise HTTPException(status_code=404, detail="Este gasto já não está na lixeira")
    return {"ok": True}

@app.delete("/lixeira")
def esvaziar_lixeira(usuario: Usuario = Depends(usuario_logado)):
    return {"ok": True, "apagados": repo.esvaziar_lixeira(usuario.id)}


# ----------------------------------------------------------------------
# Painel do admin (só contas com is_admin)
# ----------------------------------------------------------------------
@app.get("/admin/stats")
def admin_stats(admin: Usuario = Depends(admin_logado)):
    return repo.estatisticas()

@app.get("/admin/usuarios")
def admin_usuarios(admin: Usuario = Depends(admin_logado)):
    return [
        {**u, "criado_em": iso(u["criado_em"]), "ultimo_acesso": iso(u["ultimo_acesso"])}
        for u in repo.listar_usuarios()
    ]

@app.get("/admin/usuarios/{usuario_id}/despesas")
def admin_ver_despesas(usuario_id: int, admin: Usuario = Depends(admin_logado)):
    alvo = repo.usuario_por_id(usuario_id)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    repo.registrar_log_admin(admin.login, alvo.login, "abriu os registros")
    return {"login": alvo.login, "despesas": [despesa_para_dict(d) for d in repo.listar(alvo.id)]}

@app.post("/admin/usuarios/{usuario_id}/senha")
def admin_nova_senha(usuario_id: int, admin: Usuario = Depends(admin_logado)):
    """Gera uma senha temporária para quem esqueceu a dela. A pessoa troca depois em Perfil."""
    alvo = repo.usuario_por_id(usuario_id)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if alvo.id == admin.id:
        raise HTTPException(status_code=400, detail="Para a sua própria senha, use Perfil")
    temporaria = secrets.token_urlsafe(6)
    repo.definir_senha(alvo.id, gerar_hash(temporaria), secrets.token_hex(32))  # encerra as sessões dela
    repo.registrar_log_admin(admin.login, alvo.login, "gerou nova senha")
    return {"login": alvo.login, "senha_temporaria": temporaria}


@app.get("/admin/log")
def admin_log(admin: Usuario = Depends(admin_logado)):
    return [{**l, "quando": iso(l["quando"])} for l in repo.listar_log_admin(20)]
