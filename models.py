from sqlalchemy import Column, Integer, String, Float, Text, DateTime, Date, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    login = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=True)
    senha_hash = Column(String, nullable=False)
    token = Column(String, nullable=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    lixeira_dias = Column(Integer, nullable=False, default=7)  # 1 = 24 horas, 7 ou 30 dias
    moeda = Column(String, nullable=False, default="EUR")  # moeda padrão do usuário
    nome = Column(String, nullable=True)        # nome de exibição
    foto = Column(Text, nullable=True)          # imagem pequena em data URL
    preferencias = Column(Text, nullable=True)  # JSON: tema, por_pagina, aba_inicial, formato_data, semana_inicio
    criado_em = Column(DateTime, nullable=True, default=datetime.utcnow)
    ultimo_acesso = Column(DateTime, nullable=True)
    email_verificado = Column(Boolean, nullable=False, default=False)
    cod_hash = Column(String, nullable=True)      # código de 6 dígitos (guardado em hash)
    cod_tipo = Column(String, nullable=True)      # "verificar" ou "recuperar"
    cod_expira = Column(DateTime, nullable=True)
    cod_tentativas = Column(Integer, nullable=False, default=0)
    termos_em = Column(DateTime, nullable=True)   # quando aceitou os Termos e a Privacidade

class Despesa(Base):
    __tablename__ = "despesas2"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    categoria = Column(String, nullable=False)
    moeda = Column(String, nullable=False, default="EUR")  # moeda em que o gasto foi registrado
    data_hora = Column(DateTime, nullable=False, default=datetime.utcnow)
    apagado_em = Column(DateTime, nullable=True)  # preenchido = está na lixeira
    expira_em = Column(DateTime, nullable=True)   # quando a lixeira apaga de vez

class InscricaoPush(Base):
    """Aparelho que aceitou receber notificações (uma pessoa pode ter vários)."""
    __tablename__ = "inscricoes_push"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    criado_em = Column(DateTime, nullable=True, default=datetime.utcnow)

class AvisoOrcamento(Base):
    """Último nível de aviso (80 ou 100) já enviado por push, por categoria e mês."""
    __tablename__ = "avisos_orcamento"
    __table_args__ = (UniqueConstraint("usuario_id", "categoria", "mes", name="uq_aviso_orc"),)

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    categoria = Column(String, nullable=False)
    mes = Column(String, nullable=False)       # "AAAA-MM"
    limite = Column(Float, nullable=False)     # limite no momento do aviso
    nivel = Column(Integer, nullable=False)

class LogAdmin(Base):
    """Registra quando o admin abre os registros de um usuário."""
    __tablename__ = "log_admin"

    id = Column(Integer, primary_key=True)
    admin_login = Column(String, nullable=False)
    alvo_login = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    quando = Column(DateTime, nullable=False, default=datetime.utcnow)


class Recorrente(Base):
    """Gasto fixo: o app lança uma despesa sozinho a cada período."""
    __tablename__ = "recorrentes"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    moeda = Column(String, nullable=False, default="EUR")
    categoria = Column(String, nullable=False)
    frequencia = Column(String, nullable=False)   # semanal, mensal ou anual
    dia_base = Column(Integer, nullable=False)    # dia do mês original (para meses curtos)
    proxima = Column(Date, nullable=False)        # próxima cobrança a lançar
    fim = Column(Date, nullable=True)             # última data de cobrança (vazio = sem fim)
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

class Orcamento(Base):
    """Limite mensal de gastos de uma categoria."""
    __tablename__ = "orcamentos"
    __table_args__ = (UniqueConstraint("usuario_id", "categoria", name="uq_orcamento_usuario_categoria"),)

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    categoria = Column(String, nullable=False)
    limite = Column(Float, nullable=False)
    moeda = Column(String, nullable=False, default="EUR")  # moeda em que o limite foi definido
