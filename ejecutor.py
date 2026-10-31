# Ejecuta una tarea usando el motor de IA

def ejecutar_tarea(task_id, instruccion, url, password, tareas, motor):
    try:
        # Le pedimos al agente visual que haga el trabajo
        resultado = motor(
            tarea=instruccion,
            vnc_url=url,
            vnc_password=password
        )

        # Comprobamos el resultado
        estado = resultado.get("estado")

        if estado not in ("terminada", "fallida"):
            raise ValueError("Estado no valido")

        # Guardamos el resultado
        tareas[task_id]["estado"] = estado
        tareas[task_id]["resumen"] = resultado.get(
            "resumen", "Sin resumen"
        )

        # Algunas tareas requieren devolver una respuesta
        if "respuesta" in resultado:
            tareas[task_id]["respuesta"] = resultado["respuesta"]

    except Exception:
        # No mostramos contraseñas ni datos sensibles
        tareas[task_id]["estado"] = "fallida"
        tareas[task_id]["resumen"] = "No se pudo completar la tarea"
