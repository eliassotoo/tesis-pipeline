"""
Aplica los ajustes del estudio a Qodo Cover (cover-agent), fijado en el commit adcf55b.

Qué hace (y nada más):
  1. Copia docker/tesis_llm.py al mismo entorno de Python donde está instalada la herramienta.
  2. ai_caller.py: agrega a cada llamada al modelo los parámetros de tesis_llm.parametros_extra()
     (nivel de razonamiento) y anota el consumo de cada llamada con tesis_llm.registrar_uso().
  3. unit_test_validator.py: si TESIS_ANALISIS_FALLAS=local, la explicación de por qué falló una prueba
     se arma con la salida de pytest en vez de pedírsela al modelo.

No toca los criterios de filtrado de la herramienta (compila, pasa N veces, aumenta la cobertura).
Sin las variables TESIS_*, la herramienta se comporta exactamente como el original.

Cada reemplazo exige encontrar el texto original exactamente una vez: si la herramienta fuera otra
versión, el script falla en vez de aplicar un cambio a ciegas. Se puede correr más de una vez.

Uso:  python parche_qodo.py        (con el Python del entorno donde está cover-agent)
"""

import importlib.util
import shutil
import sys
from pathlib import Path

MARCA = "# [TESIS]"

REEMPLAZOS = {
    "ai_caller.py": [
        (
            '            "max_tokens": self.max_tokens,\n        }\n',
            '            "max_tokens": self.max_tokens,\n        }\n'
            f'        completion_params.update(__import__("tesis_llm").parametros_extra(self.model))  {MARCA}\n',
        ),
        (
            "        # Returns: Response, Prompt token count, and Completion token count\n"
            "        return content, prompt_tokens, completion_tokens\n",
            f'        __import__("tesis_llm").registrar_uso(caller_name, self.model, usage, prompt_tokens, completion_tokens)  {MARCA}\n'
            "        # Returns: Response, Prompt token count, and Completion token count\n"
            "        return content, prompt_tokens, completion_tokens\n",
        ),
    ],
    "unit_test_validator.py": [
        (
            "        try:\n            # Run the analysis via LLM\n",
            f'        if __import__("tesis_llm").analisis_local():  {MARCA} explicación local, sin llamar al modelo\n'
            '            return __import__("tesis_llm").resumen_local_de_falla(fail_details.get("stdout"), fail_details.get("stderr"))\n'
            "        try:\n            # Run the analysis via LLM\n",
        ),
    ],
}


def main():
    spec = importlib.util.find_spec("cover_agent")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("No se encontró cover_agent en este Python. Corré el script con el Python donde está instalado.")
    paquete = Path(list(spec.submodule_search_locations)[0])
    sitio = paquete.parent
    shutil.copy(Path(__file__).with_name("tesis_llm.py"), sitio / "tesis_llm.py")
    for nombre, cambios in REEMPLAZOS.items():
        archivo = paquete / nombre
        texto = archivo.read_text(encoding="utf-8")
        for original, nuevo in cambios:
            if nuevo in texto:
                continue  # ya aplicado
            veces = texto.count(original)
            if veces != 1:
                sys.exit(f"ERROR: en {archivo} se esperaba encontrar 1 vez el fragmento a reemplazar y aparece {veces}.\n"
                         f"¿Cambió la versión de cover-agent? Fragmento:\n{original}")
            texto = texto.replace(original, nuevo)
        archivo.write_text(texto, encoding="utf-8")
    print(f"Ajustes del estudio aplicados a {paquete} (tesis_llm en {sitio}).")


if __name__ == "__main__":
    main()
