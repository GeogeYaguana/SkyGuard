# 🛰️ SkyGuard
**SkyGuard** es una aplicación educativa que convierte datos científicos de calidad del aire en información clara y accionable para escuelas.  
Inspirada por la misión **NASA TEMPO**, utiliza mediciones atmosféricas (como PM2.5 y Ozono) para mostrar un **semáforo visual** que indica cuándo es seguro realizar actividades al aire libre.
---
## 🎯 Objetivo

Proteger la salud de los niños en entornos escolares mediante decisiones informadas basadas en datos ambientales reales.  
Cada día, miles de docentes deben decidir si los estudiantes pueden jugar o hacer deporte al aire libre sin información precisa sobre la calidad del aire.  
**SkyGuard** brinda esa respuesta en segundos.

---
## 💡 Características principales

- 🟢 **Semáforo de calidad del aire:** Muestra niveles Verde / Amarillo / Rojo según el índice AQI.  
- 🌫️ **Integración con APIs abiertas:** Usa datos de **OpenAQ** y simula datos de **NASA TEMPO**.  
- 🧭 **Interfaz simple y educativa:** Diseñada para docentes, con mensajes claros y recomendaciones prácticas.  
- 🚸 **Enfoque en salud infantil:** Advierte cuándo evitar actividades al aire libre.  
- ⚙️ **Desarrollada con:** [Streamlit](https://streamlit.io/), [Python](https://www.python.org/), [Plotly](https://plotly.com/python/).

---
## 🧬 Tecnologías utilizadas

| Área | Tecnología |
|------|-------------|
| UI / Frontend | Streamlit |
| Backend ligero | Python (requests, pandas) |
| APIs de datos | OpenAQ, NASA TEMPO (simulada) |
| Visualización | Plotly, emojis semafóricos |
| Deploy | Streamlit Cloud |

---
## 🚀 Cómo ejecutar localmente
```bash
git clone https://github.com/<tu-usuario>/skyguard.git
cd skyguard
pip install -r requirements.txt
streamlit run app.py
