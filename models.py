from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Despesa(Base):
    __tablename__ = "despesas"

    id = Column(Integer, primary_key=True)
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    categoria = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"Despesa({self.descricao!r}, {self.valor}, {self.categoria!r})"