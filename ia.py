import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class ClassificadorIA:
    def classificar(self, texto: str) -> str:
        # TEMPORÁRIO: simulando resposta da IA até termos créditos
        return "Categoria de teste"
        # --- código real (voltar depois) ---
        # response = client.chat.completions.create(
        #     model="gpt-4.1-mini",
        #     messages=[
        #         {"role": "user", "content": f"Classifica a despesa em uma categoria curta (ex: Comida, Transporte, Lazer, Contas): {texto}"}
        #     ],
        # )
        # return response.choices[0].message.content.strip()