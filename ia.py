import os
import unicodedata
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Lista fixa: a IA só pode escolher uma destas. Para mudar, edite aqui.
CATEGORIAS = ["Comida", "Mercado", "Transporte", "Moradia", "Contas", "Saúde",
              "Lazer", "Compras", "Educação", "Outros"]


def _chave(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn").strip().strip(".").lower()


_POR_CHAVE = {_chave(c): c for c in CATEGORIAS}


def categoria_valida(nome: str | None) -> str | None:
    """Devolve o nome oficial da categoria (ignorando maiúsculas e acentos), ou None."""
    return _POR_CHAVE.get(_chave(nome or ""))


class ClassificadorIA:
    def classificar(self, texto: str) -> str:
        opcoes = ", ".join(CATEGORIAS)
        try:
            response = client.chat.completions.create(
                model="gpt-6-luna",
                messages=[
                    {"role": "user", "content": (
                        f"Classifica a despesa em exatamente uma destas categorias: {opcoes}. "
                        f"Se nenhuma servir, responde Outros. Responde só com o nome da categoria: {texto}")}
                ],
            )
            bruto = response.choices[0].message.content.strip()
        except Exception as erro:
            print("Falha ao classificar:", erro)
            return "Outros"
        return categoria_valida(bruto) or "Outros"
