from fastapi import FastAPI
from pydantic import BaseModel
from models import Despesa
from database import RepositorioDespesas
from ia import ClassificadorIA

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
repositorio = RepositorioDespesas()
classificador = ClassificadorIA()


class DespesaEntrada(BaseModel):
    texto: str
    valor: float


@app.post("/despesas")
def criar_despesa(entrada: DespesaEntrada):
    categoria = classificador.classificar(entrada.texto)
    despesa = Despesa(descricao=entrada.texto, valor=entrada.valor, categoria=categoria)
    repositorio.salvar(despesa)
    return {"descricao": entrada.texto, "valor": entrada.valor, "categoria": categoria}


@app.get("/despesas")
def listar_despesas():
    todas = repositorio.listar()
    return [
        {"descricao": d.descricao, "valor": d.valor, "categoria": d.categoria}
        for d in todas
    ]