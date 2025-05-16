from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from logic import run_import_session, ImportSessionManager
from fastapi.middleware.cors import CORSMiddleware
from pyicloud import PyiCloudService
from fastapi.responses import JSONResponse
import logging
import traceback
import uuid
import sys #logs de version temporaire 
print("----------------------------------Python version:", sys.version)
# Configuration du logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuration CORS plus permissive
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # origine front local (développement)
        "https://import-icloud-frontend.vercel.app",  # origine Vercel (production)
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

@app.get("/")
async def root():
    return {"status": "ok", "message": "iCloud Importer API is running"}

class UserInput(BaseModel):
    email: str
    password: str
    destination_folder: str
    limit: int = None

class TwoFACode(BaseModel):
    email: str
    code: str
    password: str
    destination_folder: str
    limit: int = None

class SessionControl(BaseModel):
    session_id: str

class ResumeSessionInput(BaseModel):
    session_id: str
    password: str

class StopSessionInput(BaseModel):
    session_id: str

# Stock temporaire des sessions iCloud en mémoire (exemple simple)
sessions = {}
import_sessions = ImportSessionManager()

@app.post("/start")
async def start_import(data: UserInput):
    try:
        logger.debug(f"Démarrage d'une nouvelle session d'import pour {data.email}")
        
        # Tentative de connexion à iCloud
        try:
            api = PyiCloudService(data.email, data.password)
            if api.requires_2fa:
                logger.debug("2FA requis")
                return JSONResponse(
                    content={"message": "2FA required"},
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": "https://import-icloud-frontend.vercel.app",
                        "Access-Control-Allow-Credentials": "true"
                    }
                )
        except Exception as e:
            logger.error(f"Erreur lors de la connexion iCloud: {str(e)}")
            raise HTTPException(status_code=401, detail="Identifiants iCloud invalides")

        # Si pas de 2FA, on continue avec l'import
        session_id = str(uuid.uuid4())
        import_sessions.create_session(session_id, data.email, data.password, data.destination_folder, data.limit)
        import_sessions.start(session_id)
        return JSONResponse(
            content={"message": "Import lancé.", "session_id": session_id},
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "https://import-icloud-frontend.vercel.app",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors du démarrage de l'import: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/2fa")
async def validate_2fa(data: TwoFACode):
    try:
        logger.debug(f"Validation 2FA pour l'email: {data.email}")
        
        # Tentative de connexion avec 2FA
        try:
            api = PyiCloudService(data.email, data.password)
            if not api.validate_2fa_code(data.code):
                raise HTTPException(status_code=400, detail="Code 2FA invalide")
        except Exception as e:
            logger.error(f"Erreur lors de la validation 2FA: {str(e)}")
            raise HTTPException(status_code=401, detail="Erreur lors de la validation 2FA")

        # Si 2FA validé, on lance l'import
        session_id = str(uuid.uuid4())
        import_sessions.create_session(session_id, data.email, data.password, data.destination_folder, data.limit)
        import_sessions.start(session_id)
        
        return JSONResponse(
            content={"message": "Import lancé après 2FA.", "session_id": session_id},
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "https://import-icloud-frontend.vercel.app",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors de la validation 2FA: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{session_id}")
async def status_import(session_id: str):
    try:
        if session_id not in import_sessions.sessions:
            raise HTTPException(status_code=404, detail="Session non trouvée ou expirée.")
        status = import_sessions.status(session_id)
        return JSONResponse(
            content=status,
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "http://localhost:5173",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/resume")
async def resume_import(ctrl: ResumeSessionInput):
    try:
        session = import_sessions.sessions.get(ctrl.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session non trouvée ou expirée.")
        session.password = ctrl.password  # Injecte le mot de passe en mémoire
        import_sessions.resume(ctrl.session_id)
        return JSONResponse(
            content={"message": "Import repris."},
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "http://localhost:5173",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors de la reprise: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
async def stop_import(ctrl: StopSessionInput):
    try:
        session = import_sessions.sessions.get(ctrl.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session non trouvée ou expirée.")
        session.stop()  # Met le flag d'arrêt
        return JSONResponse(
            content={"message": "Import stoppé."},
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "http://localhost:5173",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors de l'arrêt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    logger.error(f"Exception HTTP: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": "http://localhost:5173",
            "Access-Control-Allow-Credentials": "true"
        }
    )
