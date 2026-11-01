# Guía paso a paso — Pipeline de evaluación (hito H4)

Tesis: *Evaluación empírica de la generación automatizada de pruebas unitarias con modelos de lenguaje en función de la cobertura de pruebas preexistente* — Soto & Arguello, FP-UNA.

> **Novedades de esta versión (diseño con dos grupos).** El pipeline procesa dos grupos de repositorios con exactamente el mismo procedimiento: **grupo A**, sin pruebas, y **grupo B**, con una suite de pruebas preexistente (la condición del estudio de Meta). El informe compara el grupo B con Meta (replicación) y el grupo A con el grupo B (el efecto de no tener pruebas). Además se redujo el consumo de la IA (sección 6.1). **Hay que reconstruir la imagen** una vez: `python -m pipeline construir-imagen`.

Esta guía se puede seguir al pie de la letra, sin saber nada previo. Cada comando se copia y se pega tal cual. Si algo falla, andá directo a la sección **10. Problemas frecuentes**.

---

## 0. Qué hace este pipeline

Toma **repositorios públicos de GitHub** de dos grupos —**A**, sin pruebas, y **B**, con pruebas escritas por sus autores—, le pide a **Gemini** (la IA de Google) que escriba pruebas unitarias usando **Qodo Cover**, las ejecuta dentro de un contenedor Docker y registra qué pasó con cada una.

| | Grupo A | Grupo B |
|---|---|---|
| Situación | El programa no tiene ninguna prueba | El programa ya tiene pruebas que pasan (cobertura inicial entre 20% y 80%) |
| Dónde escribe la IA | En un archivo de prueba mínimo (solo el import) | En el archivo de prueba real del módulo |
| Base para medir el aporte | Lo que se cubre solo con importar los módulos | Lo que ya cubre la suite completa |
| Se compara con | El grupo B | Meta (Alshahwan et al., 2024) |

```
M1 Selección  →  M2 Preparación  →  M3 Cobertura inicial  →  M4 Generación (Qodo Cover + Gemini)
      →  Clasificación preliminar (M5)  →  Almacén SQLite  →  Reporte + comparación con Meta
```

- El código del repositorio evaluado se **descarga de GitHub** en un commit fijo (así el experimento se puede repetir). No se usa ninguna carpeta local.
- La IA se consulta por **API de Gemini**. La clave se carga como secreto, nunca dentro de un archivo.
- Todo lo que corre código ajeno corre **dentro de Docker**, aislado de tu máquina.

---

## 1. Qué necesitás

| Cosa | Para qué | Costo |
|---|---|---|
| Cuenta de GitHub | Guardar el código y abrir el Codespace | Gratis |
| Cuenta de Google | Sacar la clave de Gemini en AI Studio | Gratis |
| GitHub Codespaces | Máquina Linux en la nube con Docker y Python ya instalados | Gratis dentro de la cuota mensual (120 horas-núcleo) |

Si no cargaste tarjeta en GitHub, al agotarse la cuota Codespaces se bloquea: **no cobra**. No hace falta instalar nada en tu computadora.

---

## 2. Subir el código a GitHub y abrir el Codespace (una sola vez)

1. Entrá a <https://github.com> e iniciá sesión.
2. Arriba a la derecha: **+** → **New repository**.
3. Nombre: `tesis-pipeline`. Marcá **Private**. Marcá **Add a README file**. Botón **Create repository**.
4. En la página del repo: botón verde **Code** → pestaña **Codespaces** → **Create codespace on main**.
5. Esperá 1–2 minutos. Se abre un VS Code dentro del navegador.
6. Arrastrá el archivo `tesis-pipelineV3.zip` desde tu computadora al panel izquierdo (**Explorer**). Esperá a que aparezca en la lista.
7. Abrí la terminal: menú ☰ → **Terminal** → **New Terminal**.
8. Pegá esto y apretá Enter (descomprime y deja todo en la raíz del repo):

```bash
unzip -o tesis-pipelineV3.zip && cp -r tesis-pipeline/. . && rm -rf tesis-pipeline tesis-pipelineV3.zip && ls
```

Tenés que ver: `GUIA.md  config  docker  pipeline  requirements.txt  scripts ...`

9. Guardá todo en GitHub (así no se pierde si se borra el Codespace):

```bash
git add . && git commit -m "Pipeline de evaluacion - hito H4" && git push
```

---

## 3. Instalar y verificar (cada vez que creás un Codespace nuevo)

```bash
python -m pip install -r requirements.txt
python -m pipeline verificar-entorno
```

Vas a ver una lista con `[OK]` y `[FALTA]`. Lo normal la primera vez es que falten la imagen de Docker y la clave de Gemini:

```
  [OK] Python 3.12.x
  [OK] git instalado
  [OK] Docker instalado y corriendo
  [FALTA] imagen tesis-pipeline-runner:h4-grupos construida
  [FALTA] clave GEMINI_API_KEY cargada
```

Construí la imagen (la primera vez tarda **5 a 10 minutos**; después queda guardada mientras exista el Codespace):

```bash
python -m pipeline construir-imagen
```

Al final tiene que decir `Imagen lista.`

---

## 4. Conseguir y cargar la clave de Gemini

### 4.1 Conseguir la clave (5 minutos)

1. Entrá a <https://aistudio.google.com> con una cuenta de Google.
2. Botón **Get API key** → **Create API key**. Copiá la clave (empieza con `AIza...`).
3. **Nunca** la pegues dentro de un archivo del repositorio ni la subas a GitHub.

> El plan gratuito de Gemini tiene límites (pedidos por minuto y por día) y Google puede usar lo que se envía para mejorar sus productos. Para este estudio no hay problema, porque el código que se envía es de repositorios públicos. Los límites exactos y los modelos disponibles cambian con el tiempo: se ven en AI Studio y con el comando `python -m pipeline modelos`.

### 4.2 Cargarla en Codespaces (forma segura y permanente)

1. En GitHub: tu foto (arriba a la derecha) → **Settings** → menú izquierdo **Codespaces**.
2. En **Secrets** → **New secret**. Nombre: `GEMINI_API_KEY`. Valor: la clave. En **Repository access** elegí `tesis-pipeline`. **Add secret**.
3. Reiniciá el Codespace para que tome el secreto: menú ☰ → **View** → **Command Palette** → escribí `Codespaces: Stop Current Codespace`, y después volvé a abrirlo desde el repo (**Code** → **Codespaces** → el que ya existe).

*(Alternativa rápida, dura solo hasta cerrar la terminal: `export GEMINI_API_KEY=AIza...tu-clave`)*

### 4.3 Comprobar que funciona

```bash
python -m pipeline verificar-entorno
python -m pipeline probar-llm
```

`probar-llm` tiene que imprimir `RESPUESTA DEL MODELO: funciona` (o algo parecido). Para probar con el mismo nivel de razonamiento que usa el experimento: `python -m pipeline probar-llm --razonamiento low` (muestra también cuántos tokens de razonamiento gastó). Si falla, el comando dice qué hacer:

- **Límite del plan gratuito (429):** esperá un minuto y reintentá.
- **El modelo no existe (404):** Google lo renombró. Corré `python -m pipeline modelos` para ver los nombres vigentes y probá con `python -m pipeline probar-llm --modelo gemini/NOMBRE`.
- **Problema con la clave:** revisá que esté copiada completa y sin espacios.

---

## 5. Primera prueba: un repositorio de cada grupo

```bash
python -m pipeline prueba
```

Descarga `pyckify` (grupo A, sin pruebas) y `nb-autodoc` (grupo B, con 31 pruebas que cubren el 63% del código) desde GitHub, en commits fijos, verifica los criterios del estudio y le pide a Gemini pruebas para 1 módulo de cada uno. Tarda **pocos minutos** y gasta centavos (tope de US$ 0,25 por proyecto). Antes de empezar confirma que la IA responde con la configuración del experimento; si no, se detiene y explica por qué. Es la ejecución pensada para mostrar en vivo.

La terminal muestra cinco pasos en lenguaje simple:

1. **Paso 1/5**: descarga el proyecto y revisa si cumple los criterios del estudio.
2. **Paso 2/5**: lo prepara, instalando lo que necesita.
3. **Paso 3/5**: mide cuánto código revisan sus pruebas actuales (grupo A: 0 %; grupo B: lo que cubre su suite, que además tiene que pasar).
4. **Paso 4/5**: le pide pruebas a la IA a través de Qodo Cover, archivo por archivo, en rondas.
5. **Paso 5/5**: mide cuánto código quedó revisado y clasifica cada prueba.

Al final aparece un **resumen** con el embudo de cada proyecto separado por grupo, el grupo B frente a Meta, el grupo A frente al grupo B, el costo estimado y la ruta del informe.

Si querés usar otro modelo sin tocar archivos: `python -m pipeline prueba --modelo gemini/NOMBRE`. Si querés ver los comandos y la salida cruda de Qodo Cover: agregá `--detalle`.

### 5.1 El informe para leer

```bash
python -m pipeline ver-informe
```

Es una página web que explica la ejecución para alguien que no conoce el proyecto: qué hizo el sistema, el embudo de cada proyecto, ejemplos reales de pruebas buenas y malas, y la comparación con Meta. En Codespaces aparece un aviso abajo a la derecha: **Open in Browser** (si no aparece: pestaña **PORTS**, ícono del globo). Para cerrarlo: `Ctrl + C`.

El archivo queda en `resultados/<experimento>/informe.html`. No necesita internet: se puede descargar (clic derecho → **Download**) y abrir en cualquier PC, o imprimir a PDF desde el navegador.

> Con un proyecto por grupo y una corrida, las comparaciones son una **primera mirada**, no una conclusión: todavía no hay estadística. El grupo A **no** se compara con Meta: partiendo de cero casi cualquier prueba válida aporta cobertura, así que no son las mismas condiciones (observación de la tutora).

### 5.2 Lo técnico (para nosotros)

```bash
python -m pipeline reporte --tecnico          # tabla por grupo, comparaciones y consumo de la IA por tipo de llamada
python -m pipeline ver-pruebas --limite 10    # código de las pruebas y su clasificación
python -m pipeline ver-pruebas --categoria no_compila
```

**Ver la base de datos**: abrí `resultados/resultados.db` en el panel izquierdo (si VS Code pide instalar una extensión para SQLite, aceptá). Las tablas son `ejecuciones`, `seleccion`, `corridas` y `pruebas`. También quedan CSV en `resultados/prueba/csv/`.

**Ver todo lo que dejó cada corrida**: `resultados/prueba/pyckify/corrida_1/` contiene la orden de trabajo (`orden.json`), el resultado (`resultado.json`), los reportes de cobertura XML, las pruebas generadas (`tests_generados/`) y, por cada módulo, el log completo de Qodo Cover y su reporte HTML.

---

## 6. El piloto: repositorios de los dos grupos

Cuando la primera prueba funcione:

```bash
python -m pipeline ejecutar config/piloto.yaml
```

Los repositorios están en `config/piloto.yaml`: grupo A `kodi-cli`, `pyckify`, `vertopal-cli`; grupo B `nb-autodoc`. Tarda entre 20 y 60 minutos según el modelo. El plan gratuito limita los pedidos por minuto: si aparece un error `429` o `RESOURCE_EXHAUSTED`, Qodo Cover reintenta solo; si igual falla, ese módulo queda registrado con su error y el pipeline sigue con el siguiente (RF-13).

**Para agregar o cambiar un repositorio**, copiá un bloque de la lista `repositorios` del YAML y completá los cuatro datos (todos obligatorios):

```yaml
  - nombre: nombre-corto
    grupo: A                        # A = sin pruebas · B = con suite de pruebas preexistente
    url: https://github.com/USUARIO/REPO.git
    commit: <hash completo del commit, 40 caracteres>
    fuente: carpeta_del_codigo      # carpeta dentro del repo donde está el código Python
```

En el grupo B, el pipeline instala también las dependencias de prueba que declare el proyecto (extras `[test]`/`[dev]` de `pyproject.toml`, `[dependency-groups]` o `requirements-test.txt`), exige que su suite pase y que la cobertura inicial esté entre 20% y 80% (si no, lo registra como `suite_falla` o `fuera_de_rango` y sigue). A cada módulo le asocia su archivo de prueba (`test_<módulo>.py`, o el que más lo importa); si hace falta, se fija a mano con `archivos_prueba: {paquete/modulo.py: tests/test_modulo.py}`.

El commit fijo se obtiene en GitHub: página del repo → **Commits** → ícono de copiar junto al último commit. Fijarlo es lo que permite repetir el experimento (RNF-01).

Para buscar candidatos nuevos: `python -m pipeline buscar --grupo A` o `--grupo B` (deja una lista en `resultados/candidatos_<grupo>.csv` para revisar a mano). Ojo con el grupo B: en la búsqueda del 01/11/2026 la mayoría de los proyectos con pruebas tenían más del 85% de cobertura, por encima del rango propuesto.

### 6.1 Consumo de la IA (tokens)

Leyendo el código de Qodo Cover se encontraron dos fuentes de consumo que no tienen que ver con escribir pruebas, y se controlan desde el YAML (sección `herramienta`), con el **mismo valor para los dos grupos**:

| Opción | Qué hace | Por qué |
|---|---|---|
| `analisis_fallas: local` | Cuando una prueba falla, la explicación que la herramienta le pasa a la IA en la ronda siguiente se arma con las líneas de error de pytest. | Con `ia` (original), **cada prueba que falla** genera una llamada extra con el archivo fuente y el de pruebas completos. Con muchas fallas es una parte grande del gasto. No cambia ningún criterio de filtrado. |
| `razonamiento: low` | Acota el «thinking» del modelo (minimal, low, medium, high, disable; vacío = el del modelo). | Gemini cobra el razonamiento interno como tokens de **salida**, los más caros. |

Además, cada llamada a la IA queda anotada (`uso_llm.jsonl` en la carpeta de cada módulo) y `reporte --tecnico` muestra el consumo por tipo de llamada: así se ve exactamente en qué se gastan los tokens. Para gastar menos en una demo: bajar `max_modulos` y `max_iteraciones`. Los ajustes viven en `docker/tesis_llm.py` y se aplican con `docker/parche_qodo.py` al construir la imagen; sin las variables `TESIS_*` la herramienta se comporta exactamente como el original.

---

## 7. Trabajar en equipo (compartir con tu compañero)

1. En GitHub, en el repositorio `tesis-pipeline`: **Settings** → **Collaborators** → **Add people** → su usuario de GitHub o su correo. Va a recibir una invitación por correo.
2. Cuando la acepte, abre su **propio Codespace** desde el repo (**Code** → **Codespaces** → **Create codespace on main**) y sigue esta guía desde la sección 3.
3. **Cada persona usa su propia clave de Gemini** (sección 4). Los secretos de Codespaces son personales y no se comparten por el repositorio. Así tampoco se suman los límites de uso de una sola clave.
4. Para compartir cambios, el flujo habitual: `git pull` antes de empezar y `git add . && git commit -m "mensaje" && git push` al terminar.
5. `resultados/` y `.env` están excluidos de Git a propósito (pesan mucho o tienen secretos). Para pasarse resultados, usen el informe HTML (se descarga) o los CSV.

---

## 8. Plan B: sin Docker

Si por algún motivo Docker no anda, el pipeline puede correr sin contenedores (modo depuración: NO cumple el aislamiento RNF-02, pero muestra el flujo igual):

```bash
python -m pip install -r requirements-sin-docker.txt
python -m pipeline prueba --sin-docker
```

---

## 9. Mapa del código (qué hace cada archivo)

| Archivo | Módulo del documento | Requisitos | Qué hace |
|---|---|---|---|
| `pipeline/cli.py` | Orquestador (interfaz) | — | Comandos de la línea de comandos (Typer). |
| `pipeline/config.py` | Orquestador | RNF-01, RNF-05 | Lee el YAML, completa valores por defecto, exige repositorios públicos de GitHub con commit fijo, calcula una huella de la configuración. |
| `pipeline/seleccion.py` | M1 | RF-01, RF-02 | `buscar`: candidatos vía API de GitHub. `verificar`: descarga en el commit fijo y chequea tamaño (pygount), ausencia de pruebas, dependencias, licencia y actividad; registra cada criterio y cada descarte. |
| `pipeline/ejecucion.py` | Orquestador | RF-05, RF-13, RNF-02, RNF-04 | Lanza un contenedor por repo y corrida con límites de CPU, memoria y tiempo; si algo falla, lo registra y sigue. Pasa la clave de API por variable de entorno (nunca a disco). |
| `scripts/trabajo_repo.py` | M2, M3, M4 | RF-03, RF-04, RF-06, RF-09, RF-10 | Corre **dentro** del contenedor: instala dependencias, mide cobertura inicial, arma la línea base (A: archivos semilla; B: suite preexistente), ejecuta Qodo Cover por módulo, extrae **todas** las pruebas (aceptadas y descartadas) de la base interna de la herramienta y mide la cobertura final. |
| `docker/tesis_llm.py`, `docker/parche_qodo.py` | M4 | RF-10, RNF-04, RNF-05 | Ajustes documentados sobre Qodo Cover: nivel de razonamiento, explicación local de fallas y registro del consumo por llamada. |
| `pipeline/clasificacion.py` | M5 (preliminar) | RF-07 | Clasifica cada prueba: aceptada / sin_aporte / falla / no_compila, y detecta con el módulo `ast` si compara el resultado con un valor esperado (verificación significativa, hipótesis H2). |
| `pipeline/almacen.py` | Almacén | RF-11, RNF-03 | Base SQLite con 4 tablas. |
| `pipeline/reporte.py` | M6 (preliminar) | RF-11, RF-12 | Resumen simple en la terminal; tabla técnica por repositorio (`--tecnico`), comparación descriptiva con Meta, exportación a CSV. |
| `pipeline/informe.py` | M6 (preliminar) | RF-12 | Informe HTML en lenguaje simple: embudo por proyecto, cobertura, ejemplos reales y comparación con Meta. |
| `docker/Dockerfile` | M2 | RNF-01, RNF-02 | Imagen con Python 3.12, pytest, coverage y Qodo Cover fijado a un commit, con versiones congeladas en `qodo-constraints.txt`. |
| `config/prueba.yaml`, `config/piloto.yaml` | — | RNF-01 | Configuraciones versionadas. |

**Dos intérpretes de Python dentro del contenedor (decisión de diseño).** Qodo Cover vive en su propio entorno virtual (`/opt/qodo`) y el repositorio evaluado se instala en el Python del sistema. Así, si el repositorio pide versiones de librerías que chocan con las de Qodo Cover, ninguno rompe al otro.

---

## 10. Problemas frecuentes

| Síntoma | Solución |
|---|---|
| `Docker no está disponible` | En Codespaces: esperá 1 minuto después de abrirlo y reintentá. En Windows: abrí Docker Desktop y esperá a que diga *Engine running*. |
| `No existe la imagen tesis-pipeline-runner:h4-grupos` | `python -m pipeline construir-imagen` (cambió el Dockerfile: hay que reconstruirla una vez) |
| Un proyecto del grupo B queda `suite_falla` | Sus pruebas no pasan en el entorno aislado (falta alguna dependencia de prueba o necesitan algo externo). Es un descarte del estudio; el motivo queda en `corridas.error`. |
| Un proyecto queda `fuera_de_rango` | Su cobertura inicial no está en el rango de su grupo (A: < 10%; B: 20–80%). Queda registrado en la tabla `seleccion`. |
| `probar-llm` falla con `--razonamiento` | El modelo no acepta ese nivel. Probá otro, o dejá `razonamiento: ""` en el YAML. |
| `pip install -r requirements.txt` falla con `pygount==3.2.0Pygments` | Corregido en esta versión (faltaba un salto de línea). |
| La construcción de la imagen falla descargando algo | Problema de red momentáneo: volvé a correr el comando. |
| `No module named typer` (o yaml, github, pygount) | `python -m pip install -r requirements.txt` (si da error de permisos: agregá `--user`). |
| `Falta la clave de API` / `GEMINI_API_KEY` | Cargala (sección 4.2) y reiniciá el Codespace, o usá `export GEMINI_API_KEY=...`. |
| `429` / `RESOURCE_EXHAUSTED` / `rate limit` | Límite del plan gratuito. Esperá un minuto, bajá `max_modulos` en el YAML o probá al día siguiente. |
| `probar-llm` dice que el modelo no existe (404) | `python -m pipeline modelos` y usá uno de la lista con `--modelo gemini/NOMBRE`. |
| `No se pudo descargar` un repositorio | Revisá la URL y el commit del YAML (el repo tiene que ser público). |
| Un repositorio queda `no_preparable` | Sus dependencias no se instalan solas: es un dato del estudio (criterio de exclusión). El motivo queda en la tabla `corridas`, columna `error`. |
| `buscar` se queda "colgado" | Sin `GITHUB_TOKEN` GitHub permite 60 consultas por hora. En Codespaces el token ya está; en tu PC creá uno en GitHub → Settings → Developer settings → Personal access tokens y hacé `export GITHUB_TOKEN=...`. |
| Se agotan las horas de Codespaces | Pará el Codespace cuando no lo uses: ☰ → Command Palette → `Codespaces: Stop Current Codespace`. Se apaga solo a los 30 minutos sin uso. |

---

## 11. Opción B: tu PC con Windows

1. Abrí **PowerShell como administrador** y ejecutá `wsl --install`. Reiniciá. Al volver se abre Ubuntu: creá usuario y contraseña.
2. Instalá **Docker Desktop** (<https://www.docker.com/products/docker-desktop/>). En *Settings → Resources → WSL integration* activá **Ubuntu**.
3. Abrí la terminal de **Ubuntu** (no PowerShell) y ejecutá:

```bash
sudo apt update && sudo apt install -y python3-venv git unzip
git clone https://github.com/TU-USUARIO/tesis-pipeline.git
cd tesis-pipeline
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pipeline verificar-entorno
```

4. De ahí en adelante, los mismos comandos de las secciones 3 a 6. Para la clave de API, copiá `.env.ejemplo` como `.env` y completala (`.env` está en `.gitignore`, no se sube). Cada vez que abras una terminal nueva: `cd tesis-pipeline && source .venv/bin/activate`.

---

## 12. Hallazgos del desarrollo y decisiones a documentar en el anteproyecto

Estos puntos salieron de leer el código de Qodo Cover y de probar el pipeline. Conviene incorporarlos al documento (sección 3.4) porque muestran que las decisiones están justificadas.

1. **Qodo Cover aborta si no hay pruebas.** pytest devuelve el código 5 cuando no encuentra ninguna prueba (el caso del archivo semilla), y la herramienta exige código 0 en la corrida inicial. Solución: el comando de prueba traduce el 5 a 0 (`scripts/trabajo_repo.py`, función `comando_pytest`). Forma parte de la operacionalización de "partir sin pruebas".
2. **La herramienta lee el archivo de pruebas una sola vez, al inicio.** En las iteraciones siguientes el modelo no ve las pruebas ya aceptadas, por lo que puede regenerar pruebas equivalentes, que luego se descartan por no aumentar la cobertura. Esto afecta la métrica de aporte y debe declararse como característica de la implementación (amenaza "Diferencia de implementación", Tabla 5).
3. **La temperatura está fija en 0,2 en el código de la herramienta** (no es configurable por parámetro). Coincide con el valor tentativo de la Tabla 8.
4. **La herramienta registra con el mismo motivo ("Test failed") a una prueba que no compila, a una que falla y a una inestable.** La clasificación de este hito los separa leyendo la salida de pytest, pero la separación definitiva requiere la re-ejecución independiente del M5 (hito H5).
5. **Docker por línea de comandos en vez del SDK de Python.** El orquestador invoca `docker run` directamente: permite mostrar la salida en vivo y depurar copiando el comando. Actualizar la Tabla 6 (decía "SDK de Docker para Python").
6. **Criterio de dependencias.** Un repositorio sin archivo de dependencias pero que solo usa la biblioteca estándar se considera apto (no hay nada que instalar). Antes quedaba excluido.
7. **Módulos seleccionados.** Se eligen los de más líneas que definan funciones o clases; un `__init__.py` con lógica propia se incluye (en `pyckify`, la mayor parte del código está ahí).
8. **Límite de costo (RNF-04).** Se controla entre módulo y módulo: Qodo Cover no permite cortar a mitad de una iteración.
9. **Imagen base.** Para fijarla del todo, después de la primera construcción ejecutar
   `docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'`
   y reemplazar en `docker/Dockerfile` la línea `FROM python:3.12-slim-bookworm` por `FROM python:3.12-slim-bookworm@sha256:...` (Tabla 7).
10. **Repositorios del piloto**: `kodi-cli`, `pyckify` y `vertopal-cli` (commits en `config/piloto.yaml`). Los tres cumplen los criterios automáticos. Observación para el informe: `kodi-cli` y `vertopal-cli` son clientes de servicios externos (Kodi y la API de Vertopal); sirven para el piloto, pero hay que revisar si el criterio de exclusión "requiere servicios externos" los deja afuera del conjunto final.
11. **Consumo de tokens.** El prompt incluye el archivo fuente completo, así que el consumo crece con el tamaño del módulo. La primera cota real sale de la prueba con Gemini (columnas de tokens en `python -m pipeline reporte --tecnico`); sirve para estimar el costo del experimento final y para ver si el plan gratuito alcanza.
12. **Modelo gratuito.** Los nombres de modelo y los límites del plan gratuito de Gemini cambian con el tiempo. Por eso el modelo está en el YAML (y se puede reemplazar con `--modelo`), y el comando `modelos` consulta la lista vigente. El modelo usado en cada ejecución queda registrado en la tabla `ejecuciones`; hay que citarlo con su fecha en el informe.
13. **Grupo B y comparabilidad con Meta.** En el grupo B la herramienta extiende el archivo de prueba real y el comando de prueba corre la suite completa: una prueba solo «aporta» si cubre algo que ninguna prueba preexistente cubría, como en el estudio de referencia. Se agrega `--cov-fail-under=0` para que un mínimo de cobertura configurado por el proyecto no haga «fallar» la suite.
14. **Consumo.** Cada prueba fallida disparaba una llamada extra a la IA para explicar el error (`extract_error_message` de Qodo Cover). Se reemplaza por un resumen local de la salida de pytest (`analisis_fallas: local`). Declararlo como adaptación de la implementación (Tabla 16).
15. **Fixture inexistente.** Una prueba que pide un fixture que no existe ahora se clasifica como «no compila» (Tabla 12); antes caía como «falla».

## 13. Lo que corresponde a los hitos siguientes (Proyecto II)

- **H5**: M5 definitivo (re-ejecutar cada prueba candidata aislada, sin red, N veces), integración completa, 3 corridas por repositorio.
- **H6**: ejecución sobre los 10–15 repositorios por grupo.
- **H7**: M6 completo (Wilcoxon, bootstrap, tamaño del efecto, kappa de Cohen) con pandas/SciPy.
