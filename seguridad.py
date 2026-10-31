import os
import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

esquema = HTTPBearer(auto_error=False)


def verificar_token(
    credenciales: HTTPAuthorizationCredentials | None = Security(esquema)
):
    token_esperado = os.getenv("FRONTIER_TEAM_TOKEN")

    if not token_esperado:
        raise HTTPException(
            status_code=503,
            detail="Token del servidor no configurado"
        )

    if (
        credenciales is None
        or not secrets.compare_digest(
            credenciales.credentials,
            token_esperado
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="No autorizado",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True
