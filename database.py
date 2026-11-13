import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from models import Base, Despesa, Usuario

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
        self._migrar()
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _migrar(self) -> None:
        # Ajusta a tabela de usuários antiga (só age se faltar a coluna "login")
        colunas = [c["name"] for c in inspect(self.engine).get_columns("usuarios")]
        with self.engine.begin() as conn:
            if "login" not in colunas:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN login VARCHAR"))
                conn.execute(text("UPDATE usuarios SET login = email"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_usuarios_login ON usuarios (login)"))
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("ALTER TABLE usuarios ALTER COLUMN email DROP NOT NULL"))

    def salvar(self, objeto) -> None:
        with self.Session() as session:
            session.add(objeto)
            session.commit()

    def listar(self, usuario_id: int) -> list[Despesa]:
        with self.Session() as session:
            return session.query(Despesa).filter_by(usuario_id=usuario_id).all()

    def buscar_despesa(self, despesa_id: int, usuario_id: int):
        with self.Session() as session:
            return session.query(Despesa).filter_by(id=despesa_id, usuario_id=usuario_id).first()

    def apagar_despesa(self, despesa_id: int, usuario_id: int) -> bool:
        with self.Session() as session:
            despesa = session.query(Despesa).filter_by(id=despesa_id, usuario_id=usuario_id).first()
            if not despesa:
                return False
            session.delete(despesa)
            session.commit()
            return True

    def atualizar_despesa(self, despesa_id: int, usuario_id: int, descricao: str,
                          valor: float, categoria: str, data_hora=None) -> bool:
        with self.Session() as session:
            despesa = session.query(Despesa).filter_by(id=despesa_id, usuario_id=usuario_id).first()
            if not despesa:
                return False
            despesa.descricao = descricao
            despesa.valor = valor
            despesa.categoria = categoria
            if data_hora is not None:
                despesa.data_hora = data_hora
            session.commit()
            return True

    def usuario_por_login(self, login: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(login=login).first()

    def usuario_por_email(self, email: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(email=email).first()

    def usuario_por_token(self, token: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(token=token).first()

    def salvar_token(self, usuario_id: int, token: str) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.token = token
            session.commit()
