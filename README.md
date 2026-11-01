# Pipeline de evaluación — Tesis FP-UNA (Soto & Arguello)

Evaluación empírica de la generación automatizada de pruebas unitarias con modelos de lenguaje
en función de la cobertura de pruebas preexistente: un estudio de replicación.

El pipeline descarga **repositorios públicos de GitHub** (en un commit fijo) de dos grupos —**A**, sin
pruebas, y **B**, con una suite de pruebas preexistente—, verifica los criterios de inclusión de cada uno
y le pide a **Gemini** que genere pruebas unitarias con Qodo Cover dentro de un contenedor Docker. Los
resultados quedan en SQLite y en un informe HTML que compara el grupo B con Meta (replicación) y el grupo
A con el grupo B (el efecto de no tener pruebas).

**Empezá por [GUIA.md](GUIA.md)**: explica paso a paso cómo levantar el entorno en GitHub Codespaces,
cargar la clave de Gemini y correr la primera prueba.

```bash
python -m pipeline verificar-entorno
python -m pipeline construir-imagen
python -m pipeline probar-llm        # confirma que la clave de Gemini funciona
python -m pipeline prueba            # 1 repo del grupo A + 1 del grupo B, pocos minutos
python -m pipeline ver-informe       # informe para leer, en el navegador
```

Estado: hito H4 (implementación inicial del pipeline y prueba piloto).
