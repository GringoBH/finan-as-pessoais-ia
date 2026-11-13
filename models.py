from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
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

class LogAdmin(Base):
    """Registra quando o admin abre os registros de um usuário."""
    __tablename__ = "log_admin"

    id = Column(Integer, primary_key=True)
    admin_login = Column(String, nullable=False)
    alvo_login = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    quando = Column(DateTime, nullable=False, default=datetime.utcnow)
