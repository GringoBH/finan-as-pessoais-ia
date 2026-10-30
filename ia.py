import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class ClassificadorIA:
    def classificar(self, texto: str) -> str:
        response = client.chat.completions.create(
            model="gpt-6-luna",
            messages=[
                {"role": "user", "content": f"Classifica a despesa em uma categoria curta (ex: Comida, Transporte, Lazer, Contas). Responde só com o nome da categoria: {texto}"}
            ],
        )
        return response.choices[0].message.content.strip()