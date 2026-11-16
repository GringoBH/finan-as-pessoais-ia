import os
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text, func
from sqlalchemy.orm import sessionmaker
from models import Base, Despesa, Usuario, LogAdmin, Recorrente, Orcamento, InscricaoPush, AvisoOrcamento
from recorrencia import proxima_data

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
                "moeda": "VARCHAR NOT NULL DEFAULT 'EUR'",
                "nome": "VARCHAR",
                "foto": "TEXT",
                "preferencias": "TEXT",
                "criado_em": tipo_data,
                "ultimo_acesso": tipo_data,
                "email_verificado": "BOOLEAN NOT NULL DEFAULT FALSE",
                "cod_hash": "VARCHAR",
                "cod_tipo": "VARCHAR",
                "cod_expira": tipo_data,
                "cod_tentativas": "INTEGER NOT NULL DEFAULT 0",
                "termos_em": tipo_data,
            }
            for nome, definicao in novas_u.items():
                if nome not in colunas_u:
                    conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN {nome} {definicao}"))
            colunas_d = [c["name"] for c in inspetor.get_columns("despesas2")]
            for nome in ("apagado_em", "expira_em"):
                if nome not in colunas_d:
                    conn.execute(text(f"ALTER TABLE despesas2 ADD COLUMN {nome} {tipo_data}"))
            if "moeda" not in colunas_d:
                conn.execute(text("ALTER TABLE despesas2 ADD COLUMN moeda VARCHAR NOT NULL DEFAULT 'EUR'"))
            colunas_r = [c["name"] for c in inspetor.get_columns("recorrentes")]
            if "fim" not in colunas_r:
                conn.execute(text("ALTER TABLE recorrentes ADD COLUMN fim DATE"))
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
                          valor: float, categoria: str, data_hora=None, moeda: str | None = None) -> bool:
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
            if moeda:
                despesa.moeda = moeda
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Gastos fixos (recorrentes)
    # ------------------------------------------------------------------
    def listar_recorrentes(self, usuario_id: int) -> list[Recorrente]:
        with self.Session() as session:
            return (session.query(Recorrente).filter(Recorrente.usuario_id == usuario_id)
                    .order_by(Recorrente.descricao).all())

    def buscar_recorrente(self, rec_id: int, usuario_id: int):
        with self.Session() as session:
            return (session.query(Recorrente)
                    .filter(Recorrente.id == rec_id, Recorrente.usuario_id == usuario_id).first())

    def atualizar_recorrente(self, rec_id: int, usuario_id: int, **campos) -> bool:
        with self.Session() as session:
            rec = (session.query(Recorrente)
                   .filter(Recorrente.id == rec_id, Recorrente.usuario_id == usuario_id).first())
            if not rec:
                return False
            for nome, valor in campos.items():
                setattr(rec, nome, valor)
            session.commit()
            return True

    def apagar_recorrente(self, rec_id: int, usuario_id: int) -> bool:
        with self.Session() as session:
            n = (session.query(Recorrente)
                 .filter(Recorrente.id == rec_id, Recorrente.usuario_id == usuario_id)
                 .delete(synchronize_session=False))
            session.commit()
            return n > 0

    def lancar_recorrentes(self, usuario_id: int, hoje: date) -> int:
        """Cria os gastos que já venceram (inclusive atrasados) e avança a próxima data. Devolve quantos criou."""
        criados = 0
        with self.Session() as session:
            vencidos = (session.query(Recorrente)
                        .filter(Recorrente.usuario_id == usuario_id, Recorrente.ativo.is_(True),
                                Recorrente.proxima <= hoje)
                        .with_for_update().all())
            for rec in vencidos:
                voltas = 0
                while rec.proxima <= hoje and (rec.fim is None or rec.proxima <= rec.fim) and voltas < 60:  # limite de segurança
                    session.add(Despesa(usuario_id=usuario_id, descricao=rec.descricao, valor=rec.valor,
                                        moeda=rec.moeda, categoria=rec.categoria,
                                        data_hora=datetime(rec.proxima.year, rec.proxima.month, rec.proxima.day, 12)))
                    rec.proxima = proxima_data(rec.proxima, rec.frequencia, rec.dia_base)
                    criados += 1
                    voltas += 1
                if rec.fim is not None and rec.proxima > rec.fim:
                    rec.ativo = False  # chegou ao fim: não lança mais
            if vencidos:
                session.commit()
        return criados

    # ------------------------------------------------------------------
    # Orçamentos por categoria
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Notificações push
    # ------------------------------------------------------------------
    def salvar_inscricao(self, usuario_id: int, endpoint: str, p256dh: str, auth: str) -> None:
        with self.Session() as session:
            ins = session.query(InscricaoPush).filter(InscricaoPush.endpoint == endpoint).first()
            if ins:  # o mesmo aparelho pode trocar de conta
                ins.usuario_id, ins.p256dh, ins.auth = usuario_id, p256dh, auth
            else:
                session.add(InscricaoPush(usuario_id=usuario_id, endpoint=endpoint, p256dh=p256dh, auth=auth))
            session.commit()

    def listar_inscricoes(self, usuario_id: int) -> list[InscricaoPush]:
        with self.Session() as session:
            return session.query(InscricaoPush).filter(InscricaoPush.usuario_id == usuario_id).all()

    def apagar_inscricao(self, endpoint: str, usuario_id: int | None = None) -> None:
        with self.Session() as session:
            consulta = session.query(InscricaoPush).filter(InscricaoPush.endpoint == endpoint)
            if usuario_id is not None:
                consulta = consulta.filter(InscricaoPush.usuario_id == usuario_id)
            consulta.delete(synchronize_session=False)
            session.commit()

    def subir_aviso_orcamento(self, usuario_id: int, categoria: str, mes: str, limite: float, nivel: int) -> bool:
        """Registra o nível avisado. Devolve True só se é um aviso novo (nível maior ou limite diferente)."""
        with self.Session() as session:
            aviso = (session.query(AvisoOrcamento)
                     .filter_by(usuario_id=usuario_id, categoria=categoria, mes=mes).first())
            if aviso and aviso.nivel >= nivel and abs(aviso.limite - limite) < 0.01:
                return False
            if aviso:
                aviso.nivel, aviso.limite = nivel, limite
            else:
                session.add(AvisoOrcamento(usuario_id=usuario_id, categoria=categoria, mes=mes, limite=limite, nivel=nivel))
            session.commit()
            return True

    def listar_orcamentos(self, usuario_id: int) -> list[Orcamento]:
        with self.Session() as session:
            return session.query(Orcamento).filter(Orcamento.usuario_id == usuario_id).all()

    def definir_orcamento(self, usuario_id: int, categoria: str, limite: float | None, moeda: str) -> None:
        """Cria ou atualiza o limite; limite vazio ou zero remove o orçamento."""
        with self.Session() as session:
            atual = (session.query(Orcamento)
                     .filter(Orcamento.usuario_id == usuario_id, Orcamento.categoria == categoria).first())
            if not limite or limite <= 0:
                if atual:
                    session.delete(atual)
            elif atual:
                atual.limite = limite
                atual.moeda = moeda
            else:
                session.add(Orcamento(usuario_id=usuario_id, categoria=categoria, limite=limite, moeda=moeda))
            session.commit()

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
        with self.Session() as session:  # sem diferenciar maiúsculas (contas antigas usam o email)
            return session.query(Usuario).filter(func.lower(Usuario.login) == login.strip().lower()).first()

    def usuario_por_email(self, email: str):
        with self.Session() as session:
            return session.query(Usuario).filter(func.lower(Usuario.email) == email.strip().lower()).first()

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

    def atualizar_perfil(self, usuario_id: int, nome: str | None, email: str | None) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.nome = nome
            if (email or "") != (usuario.email or ""):
                usuario.email_verificado = False  # email novo precisa ser confirmado de novo
                usuario.cod_hash = None
            usuario.email = email
            session.commit()

    # ------------------------------------------------------------------
    # Códigos por email (confirmar email e recuperar senha)
    # ------------------------------------------------------------------
    def guardar_codigo(self, usuario_id: int, hash_: str, tipo: str, expira: datetime) -> None:
        with self.Session() as session:
            u = session.get(Usuario, usuario_id)
            u.cod_hash, u.cod_tipo, u.cod_expira, u.cod_tentativas = hash_, tipo, expira, 0
            session.commit()

    def falha_no_codigo(self, usuario_id: int) -> None:
        with self.Session() as session:
            u = session.get(Usuario, usuario_id)
            u.cod_tentativas = (u.cod_tentativas or 0) + 1
            session.commit()

    def limpar_codigo(self, usuario_id: int) -> None:
        with self.Session() as session:
            u = session.get(Usuario, usuario_id)
            u.cod_hash = u.cod_tipo = u.cod_expira = None
            u.cod_tentativas = 0
            session.commit()

    def marcar_email_verificado(self, usuario_id: int) -> None:
        with self.Session() as session:
            u = session.get(Usuario, usuario_id)
            u.email_verificado = True
            u.cod_hash = u.cod_tipo = u.cod_expira = None
            u.cod_tentativas = 0
            session.commit()

    def usuario_por_id(self, usuario_id: int):
        with self.Session() as session:
            return session.get(Usuario, usuario_id)

    def definir_foto(self, usuario_id: int, foto: str | None) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.foto = foto
            session.commit()

    def definir_preferencias(self, usuario_id: int, texto_json: str) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.preferencias = texto_json
            session.commit()

    def definir_senha(self, usuario_id: int, senha_hash: str, token: str) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.senha_hash = senha_hash
            usuario.token = token  # novo token: encerra as outras sessões
            session.commit()

    def definir_moeda(self, usuario_id: int, moeda: str) -> None:
        with self.Session() as session:
            usuario = session.get(Usuario, usuario_id)
            usuario.moeda = moeda
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
            session.query(Recorrente).filter(Recorrente.usuario_id == usuario_id).delete(synchronize_session=False)
            session.query(Orcamento).filter(Orcamento.usuario_id == usuario_id).delete(synchronize_session=False)
            session.query(InscricaoPush).filter(InscricaoPush.usuario_id == usuario_id).delete(synchronize_session=False)
            session.query(AvisoOrcamento).filter(AvisoOrcamento.usuario_id == usuario_id).delete(synchronize_session=False)
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
