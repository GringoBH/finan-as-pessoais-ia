from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Despesa


class RepositorioDespesas:
    def __init__(self, arquivo: str = "financas.db"):
        self.engine = create_engine(f"sqlite:///{arquivo}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def salvar(self, despesa: Despesa) -> None:
        with self.Session() as session:
            session.add(despesa)
            session.commit()

    def listar(self) -> list[Despesa]:
        with self.Session() as session:
            return session.query(Despesa).all()