import re
from models import Despesa
from database import RepositorioDespesas
from ia import ClassificadorIA


def extrair_valor(texto: str) -> float:
    valores = re.findall(r"\d+\.?\d*", texto)
    return float(valores[0]) if valores else 0.0


def main():
    repositorio = RepositorioDespesas()
    classificador = ClassificadorIA()

    texto = input("Descreva a despesa: ")
    valor = extrair_valor(texto)
    categoria = classificador.classificar(texto)

    despesa = Despesa(descricao=texto, valor=valor, categoria=categoria)
    repositorio.salvar(despesa)

    todas = repositorio.listar()

    print("\n" + "=" * 30)
    print("     EXTRATO DE GASTOS")
    print("=" * 30)
    for d in todas:
        desc = d.descricao[:15].ljust(15)
        v_formatado = f"{d.valor:.2f}€".rjust(10)
        print(f"{desc} | {v_formatado} | {d.categoria}")
    print("-" * 30)

    total = sum(d.valor for d in todas)
    print(f"Total: {total:.2f}€".rjust(30))
    print("=" * 30)


if __name__ == "__main__":
    main()