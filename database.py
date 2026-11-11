import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Despesa

load_dotenv()


def _url_do_banco(arquivo: str) -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    return f"sqlite:///{arquivo}"


class RepositorioDespesas:
    def __init__(self, arquivo: str = "financas.db"):
        self.engine = create_engine(_url_do_banco(arquivo), pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def salvar(self, despesa: Despesa) -> None:
        with self.Session() as session:
            session.add(despesa)
            session.commit()

    def listar(self) -> list[Despesa]:
        with self.Session() as session:
            return session.query(Despesa).all()