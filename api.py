import hashlib
import os
import secrets
import json
import time
import urllib.request
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import RepositorioDespesas
from models import Despesa, Usuario
from ia import ClassificadorIA

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
MOEDAS = ("EUR", "BRL", "USD", "GBP")  # moedas aceitas; para ampliar, acrescente códigos aqui


# ----------------------------------------------------------------------
# Modelos de entrada
# ----------------------------------------------------------------------
class DespesaEntrada(BaseModel):
    texto: str
    valor: float
    data: str | None = None  # "AAAA-MM-DD"; se vier vazio, usa o momento atual
    moeda: str | None = None  # se vier vazio, usa a moeda padrão do usuário

class LoginEntrada(BaseModel):
    login: str
    senha: str

class RegistroEntrada(BaseModel):
    login: str
    senha: str
    email: str | None = None

class LixeiraConfig(BaseModel):
    dias: int

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

_cotacoes = {"quando": 0.0, "dados": None}

def buscar_cotacoes() -> dict:
    """Cotações do dia (base EUR) via Frankfurter. Guarda 6 horas; se a fonte falhar, usa a última guardada."""
    if _cotacoes["dados"] and time.time() - _cotacoes["quando"] < 6 * 3600:
        return _cotacoes["dados"]
    outras = ",".join(m for m in MOEDAS if m != "EUR")
    for url in (f"https://api.frankfurter.dev/v1/latest?base=EUR&symbols={outras}",
                f"https://api.frankfurter.app/latest?from=EUR&to={outras}"):
        try:
            with urllib.request.urlopen(url, timeout=6) as r:
                bruto = json.load(r)
            taxas = {"EUR": 1.0, **{k: float(v) for k, v in bruto["rates"].items()}}
            if all(m in taxas for m in MOEDAS):
                _cotacoes["dados"] = {"base": "EUR", "data": bruto.get("date"), "taxas": taxas}
                _cotacoes["quando"] = time.time()
                return _cotacoes["dados"]
        except Exception as erro:
            print("Falha ao buscar cotações:", erro)
    if _cotacoes["dados"]:
        return _cotacoes["dados"]
    raise HTTPException(status_code=503, detail="Cotações indisponíveis agora. Tente de novo em instantes.")

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
@app.post("/registro")
def registro(r: RegistroEntrada):
    login = r.login.strip().lower()
    email = (r.email or "").strip().lower() or None
    if len(login) < 3 or " " in login:
        raise HTTPException(status_code=400, detail="Usuário: mínimo 3 caracteres, sem espaços")
    if len(r.senha) < 6:
        raise HTTPException(status_code=400, detail="Senha: mínimo 6 caracteres")
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    if repo.usuario_por_login(login):
        raise HTTPException(status_code=400, detail="Este usuário já existe")
    if email and repo.usuario_por_email(email):
        raise HTTPException(status_code=400, detail="Este email já está em uso")
    usuario = Usuario(login=login, email=email, senha_hash=gerar_hash(r.senha),
                      is_admin=False, lixeira_dias=7, moeda="EUR")
    repo.salvar(usuario)
    aplicar_admin(usuario)
    return nova_sessao(usuario)

@app.post("/login")
def login(c: LoginEntrada):
    usuario = repo.usuario_por_login(c.login.strip().lower())
    if not usuario or not senha_correta(c.senha, usuario.senha_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
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

@app.get("/me")
def meus_dados(usuario: Usuario = Depends(usuario_logado)):
    return {
        "login": usuario.login,
        "nome": usuario.nome,
        "foto": usuario.foto,
        "preferencias": preferencias_do_usuario(usuario),
        "email": usuario.email,
        "is_admin": bool(usuario.is_admin),
        "lixeira_dias": usuario.lixeira_dias,
        "moeda": usuario.moeda or "EUR",
        "moedas": list(MOEDAS),
    }

@app.put("/me/moeda")
def configurar_moeda(c: MoedaConfig, usuario: Usuario = Depends(usuario_logado)):
    moeda = moeda_valida(c.moeda, "EUR")
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
def criar_despesa(entrada: DespesaEntrada, usuario: Usuario = Depends(usuario_logado)):
    validar_despesa(entrada)
    data_hora = data_hora_da_entrada(entrada.data)
    categoria = ia.classificar(entrada.texto)
    moeda = moeda_valida(entrada.moeda, usuario.moeda)
    repo.salvar(Despesa(usuario_id=usuario.id, descricao=entrada.texto, valor=entrada.valor,
                        categoria=categoria, moeda=moeda, data_hora=data_hora))
    return {"descricao": entrada.texto, "valor": entrada.valor, "moeda": moeda, "categoria": categoria}

@app.get("/despesas")
def listar_despesas(usuario: Usuario = Depends(usuario_logado)):
    return [despesa_para_dict(d) for d in repo.listar(usuario.id)]

@app.put("/despesas/{despesa_id}")
def editar_despesa(despesa_id: int, entrada: DespesaEntrada, usuario: Usuario = Depends(usuario_logado)):
    validar_despesa(entrada)
    atual = repo.buscar_despesa(despesa_id, usuario.id)
    if not atual:
        raise HTTPException(status_code=404, detail="Despesa não encontrada")
    # só pede nova categoria à IA se a descrição mudou
    categoria = atual.categoria if entrada.texto == atual.descricao else ia.classificar(entrada.texto)
    nova_data = data_hora_da_entrada(entrada.data) if entrada.data else None
    moeda = moeda_valida(entrada.moeda, atual.moeda)
    repo.atualizar_despesa(despesa_id, usuario.id, entrada.texto, entrada.valor, categoria, nova_data, moeda)
    return {"ok": True, "categoria": categoria}

@app.delete("/despesas/{despesa_id}")
def mover_para_lixeira(despesa_id: int, usuario: Usuario = Depends(usuario_logado)):
    """Apagar = mover para a lixeira, pelo prazo que o usuário escolheu."""
    expira = repo.mover_para_lixeira(despesa_id, usuario.id, usuario.lixeira_dias)
    if expira is None:
        raise HTTPException(status_code=404, detail="Despesa não encontrada")
    return {"ok": True, "expira_em": iso(expira)}


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

@app.get("/admin/log")
def admin_log(admin: Usuario = Depends(admin_logado)):
    return [{**l, "quando": iso(l["quando"])} for l in repo.listar_log_admin(20)]
