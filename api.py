import hashlib
import secrets
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

class DespesaEntrada(BaseModel):
    texto: str
    valor: float

class LoginEntrada(BaseModel):
    login: str
    senha: str

class RegistroEntrada(BaseModel):
    login: str
    senha: str
    email: str | None = None

def gerar_hash(senha: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"

def senha_correta(senha: str, guardado: str) -> bool:
    salt = guardado.split("$")[0]
    return secrets.compare_digest(gerar_hash(senha, salt), guardado)

def usuario_logado(authorization: str = Header(default="")) -> Usuario:
    usuario = repo.usuario_por_token(authorization) if authorization else None
    if not usuario:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return usuario

def nova_sessao(usuario: Usuario) -> dict:
    token = secrets.token_hex(32)
    repo.salvar_token(usuario.id, token)
    return {"token": token}

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
    usuario = Usuario(login=login, email=email, senha_hash=gerar_hash(r.senha))
    repo.salvar(usuario)
    return nova_sessao(usuario)

@app.post("/login")
def login(c: LoginEntrada):
    usuario = repo.usuario_por_login(c.login.strip().lower())
    if not usuario or not senha_correta(c.senha, usuario.senha_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    return nova_sessao(usuario)

@app.post("/despesas")
def criar_despesa(entrada: DespesaEntrada, usuario: Usuario = Depends(usuario_logado)):
    categoria = ia.classificar(entrada.texto)
    repo.salvar(Despesa(usuario_id=usuario.id, descricao=entrada.texto,
                        valor=entrada.valor, categoria=categoria))
    return {"descricao": entrada.texto, "valor": entrada.valor, "categoria": categoria}

@app.get("/despesas")
def listar_despesas(usuario: Usuario = Depends(usuario_logado)):
    return [
        {
            "descricao": d.descricao,
            "valor": d.valor,
            "categoria": d.categoria,
            "data_hora": d.data_hora.isoformat(),
            "dia_semana": d.data_hora.strftime("%A"),
            "semana_ano": d.data_hora.isocalendar()[1],
            "mes": d.data_hora.month,
            "ano": d.data_hora.year,
        }
        for d in repo.listar(usuario.id)
    ]