"""Backup do banco: salva todas as tabelas num arquivo JSON dentro da pasta backups/.

Como usar (no terminal do PyCharm, na pasta do projeto):
    python backup.py

Usa o mesmo DATABASE_URL do arquivo .env (o banco do Neon). O arquivo tem dados dos clientes:
NUNCA suba para o GitHub (a pasta backups/ já está no .gitignore) e guarde num lugar seguro.
"""
import json
import os
from datetime import date, datetime

from sqlalchemy import select

from database import RepositorioDespesas
from models import Base

SEM_COPIAR = {("usuarios", "token")}  # sessões abertas não precisam ir para o backup


def para_json(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def main():
    repo = RepositorioDespesas()
    dados, total = {}, 0
    with repo.engine.connect() as conexao:
        for tabela in Base.metadata.sorted_tables:
            linhas = []
            for linha in conexao.execute(select(tabela)).mappings():
                linhas.append({c: para_json(v) for c, v in linha.items() if (tabela.name, c) not in SEM_COPIAR})
            dados[tabela.name] = linhas
            total += len(linhas)
            print(f"{tabela.name}: {len(linhas)} linhas")
    os.makedirs("backups", exist_ok=True)
    caminho = os.path.join("backups", f"backup_{datetime.now():%Y-%m-%d_%H%M}.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    print(f"\nPronto: {total} linhas salvas em {caminho}")


if __name__ == "__main__":
    main()
