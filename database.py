import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text, func
from sqlalchemy.orm import sessionmaker
from models import Base, Despesa, Usuario, LogAdmin

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

    # ------------------------------------------------------------------
    # Migração: adiciona colunas que faltam em tabelas já existentes
    # ------------------------------------------------------------------
    def _migrar(self) -> None:
        tipo_data = "TIMESTAMP" if self.engine.dialect.name == "postgresql" else "DATETIME"
        with self.engine.begin() as conn:
            inspetor = inspect(conn)
            colunas_u = [c["name"] for c in inspetor.get_columns("usuarios")]
            if "login" not in colunas_u:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN login VARCHAR"))
                conn.execute(text("UPDATE usuarios SET login = email"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_usuarios_login ON usuarios (login)"))
            novas_u = {
                "is_admin": "BOOLEAN NOT NULL DEFAULT FALSE",
                "lixeira_dias": "INTEGER NOT NULL DEFAULT 7",
                "criado_em": tipo_data,
                "ultimo_acesso": tipo_data,
            }
            for nome, definicao in novas_u.items():
                if nome not in colunas_u:
                    conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN {nome} {definicao}"))
            colunas_d = [c["name"] for c in inspetor.get_columns("despesas2")]
            for nome in ("apagado_em", "expira_em"):
                if nome not in colunas_d:
                    conn.execute(text(f"ALTER TABLE despesas2 ADD COLUMN {nome} {tipo_data}"))
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("ALTER TABLE usuarios ALTER COLUMN email DROP NOT NULL"))

    # ------------------------------------------------------------------
    # Gastos
    # ------------------------------------------------------------------
    def salvar(self, objeto) -> None:
        with self.Session() as session:
            session.add(objeto)
            session.commit()

    def listar(self, usuario_id: int) -> list[Despesa]:
        """Gastos ativos (fora da lixeira)."""
        with self.Session() as session:
            return (session.query(Despesa)
                    .filter(Despesa.usuario_id == usuario_id, Despesa.apagado_em.is_(None))
                    .all())

    def buscar_despesa(self, despesa_id: int, usuario_id: int):
        with self.Session() as session:
            return (session.query(Despesa)
                    .filter(Despesa.id == despesa_id, Despesa.usuario_id == usuario_id,
                            Despesa.apagado_em.is_(None))
                    .first())

    def atualizar_despesa(self, despesa_id: int, usuario_id: int, descricao: str,
                          valor: float, categoria: str, data_hora=None) -> bool:
        with self.Session() as session:
            despesa = (session.query(Despesa)
                       .filter(Despesa.id == despesa_id, Despesa.usuario_id == usuario_id,
                               Despesa.apagado_em.is_(None))
                       .first())
            if not despesa:
                return False
            despesa.descricao = descricao
            despesa.valor = valor
            despesa.categoria = categoria
            if data_hora is not None:
                despesa.data_hora = data_hora
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Lixeira
    # ------------------------------------------------------------------
    def mover_para_lixeira(self, despesa_id: int, usuario_id: int, dias: int):
        """Devolve quando o gasto será apagado de vez, ou None se não existir."""
        with self.Session() as session:
            despesa = (session.query(Despesa)
                       .filter(Despesa.id == despesa_id, Despesa.usuario_id == usuario_id,
                               Despesa.apagado_em.is_(None))
                       .first())
            if not despesa:
                return None
            agora = datetime.utcnow()
            despesa.apagado_em = agora
            despesa.expira_em = agora + timedelta(days=dias)
            session.commit()
            return despesa.expira_em

    def listar_lixeira(self, usuario_id: int) -> list[Despesa]:
        with self.Session() as session:
            return (session.query(Despesa)
                    .filter(Despesa.usuario_id == usuario_id, Despesa.apagado_em.is_not(None),
                            Despesa.expira_em > datetime.utcnow())
                    .order_by(Despesa.apagado_em.desc())
                    .all())

    def restaurar(self, despesa_id: int, usuario_id: int) -> bool:
        with self.Session() as session:
            despesa = (session.query(Despesa)
                       .filter(Despesa.id == despesa_id, Despesa.usuario_id == usuario_id,
                               Despesa.apagado_em.is_not(None), Despesa.expira_em > datetime.utcnow())
                       .first())
            if not despesa:
                return False
            despesa.apagado_em = None
            despesa.expira_em = None
            session.commit()
            return True

    def apagar_de_vez(self, despesa_id: int, usuario_id: int) -> bool:
        with self.Session() as session:
            despesa = (session.query(Despesa)
                       .filter(Despesa.id == despesa_id, Despesa.usuario_id == usuario_id,
                               Despesa.apagado_em.is_not(None))
                       .first())
            if not despesa:
                return False
            session.delete(despesa)
            session.commit()
            return True

    def esvaziar_lixeira(self, usuario_id: int) -> int:
        with self.Session() as session:
            n = (session.query(Despesa)
                 .filter(Despesa.usuario_id == usuario_id, Despesa.apagado_em.is_not(None))
                 .delete(synchronize_session=False))
            session.commit()
            return n

    def purgar_vencidos(self) -> int:
        """Apaga de vez tudo que passou do prazo, de todos os usuários."""
        with self.Session() as session:
            n = (session.query(Despesa)
                 .filter(Despesa.expira_em.is_not(None), Despesa.expira_em <= datetime.utcnow())
                 .delete(synchronize_session=False))
            session.commit()
            return n

    # ------------------------------------------------------------------
    # Usuários
    # ------------------------------------------------------------------
    def usuario_por_login(self, login: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(login=login).first()

    def usuario_por_email(self, email: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(email=email).first()

    def usuario_por_token(self, token: str):
        with self.Session() as session:
            return session.query(Usuario).filter_by(token=token).first()

    def usuario_por_id(self, usuario_id: int):
        with self.Session() as session:
            return session.get(Usuario, usuario_id)

    def salvar_token(self, usuario_id: int, token: str) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.token = token
            session.commit()

    def registrar_acesso(self, usuario_id: int) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            if usuario:
                usuario.ultimo_acesso = datetime.utcnow()
                session.commit()

    def definir_lixeira_dias(self, usuario_id: int, dias: int) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.lixeira_dias = dias
            session.commit()

    def definir_admin(self, usuario_id: int) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.is_admin = True
            session.commit()

    def apagar_conta(self, usuario_id: int) -> None:
        """Remove o usuário e todos os gastos dele (inclusive da lixeira)."""
        with self.Session() as session:
            session.query(Despesa).filter(Despesa.usuario_id == usuario_id).delete(synchronize_session=False)
            session.query(Usuario).filter(Usuario.id == usuario_id).delete(synchronize_session=False)
            session.commit()

    # ------------------------------------------------------------------
    # Painel do admin
    # ------------------------------------------------------------------
    def estatisticas(self) -> dict:
        agora = datetime.utcnow()
        inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.Session() as session:
            def contar_usuarios(*filtros) -> int:
                return session.query(func.count(Usuario.id)).filter(*filtros).scalar() or 0

            desde = inicio_hoje - timedelta(days=13)
            serie = {}
            for i in range(14):
                serie[(desde + timedelta(days=i)).date().isoformat()] = 0
            criados = session.query(Usuario.criado_em).filter(Usuario.criado_em >= desde).all()
            for (criado,) in criados:
                chave = criado.date().isoformat()
                if chave in serie:
                    serie[chave] += 1

            return {
                "total_usuarios": contar_usuarios(),
                "online_agora": contar_usuarios(Usuario.ultimo_acesso >= agora - timedelta(minutes=5)),
                "ativos_24h": contar_usuarios(Usuario.ultimo_acesso >= agora - timedelta(hours=24)),
                "ativos_7d": contar_usuarios(Usuario.ultimo_acesso >= agora - timedelta(days=7)),
                "novos_hoje": contar_usuarios(Usuario.criado_em >= inicio_hoje),
                "novos_7d": contar_usuarios(Usuario.criado_em >= agora - timedelta(days=7)),
                "novos_30d": contar_usuarios(Usuario.criado_em >= agora - timedelta(days=30)),
                "gastos_ativos": session.query(func.count(Despesa.id)).filter(Despesa.apagado_em.is_(None)).scalar() or 0,
                "gastos_na_lixeira": session.query(func.count(Despesa.id)).filter(Despesa.apagado_em.is_not(None)).scalar() or 0,
                "novos_por_dia": [{"dia": dia, "n": n} for dia, n in serie.items()],
            }

    def listar_usuarios(self) -> list[dict]:
        with self.Session() as session:
            ativos = dict(session.query(Despesa.usuario_id, func.count(Despesa.id))
                          .filter(Despesa.apagado_em.is_(None))
                          .group_by(Despesa.usuario_id).all())
            lixo = dict(session.query(Despesa.usuario_id, func.count(Despesa.id))
                        .filter(Despesa.apagado_em.is_not(None))
                        .group_by(Despesa.usuario_id).all())
            lista = [
                {
                    "id": u.id, "login": u.login, "email": u.email, "is_admin": bool(u.is_admin),
                    "criado_em": u.criado_em, "ultimo_acesso": u.ultimo_acesso,
                    "n_gastos": ativos.get(u.id, 0), "n_lixeira": lixo.get(u.id, 0),
                }
                for u in session.query(Usuario).all()
            ]
        lista.sort(key=lambda x: x["ultimo_acesso"] or datetime.min, reverse=True)
        return lista

    def registrar_log_admin(self, admin_login: str, alvo_login: str, acao: str) -> None:
        with self.Session() as session:
            session.add(LogAdmin(admin_login=admin_login, alvo_login=alvo_login, acao=acao))
            session.commit()

    def listar_log_admin(self, limite: int = 20) -> list[dict]:
        with self.Session() as session:
            linhas = session.query(LogAdmin).order_by(LogAdmin.quando.desc()).limit(limite).all()
            return [{"admin": l.admin_login, "alvo": l.alvo_login, "acao": l.acao, "quando": l.quando} for l in linhas]
