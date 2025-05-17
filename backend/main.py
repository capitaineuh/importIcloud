from fastapi import FastAPI, HTTPException, Response, BackgroundTasks, Request
from pydantic import BaseModel, EmailStr, constr
from logic import run_import_session, ImportSessionManager, ImportSession
from fastapi.middleware.cors import CORSMiddleware
from pyicloud import PyiCloudService
from fastapi.responses import JSONResponse, StreamingResponse
import logging
import traceback
import uuid
import sys #logs de version temporaire 
import uvicorn
import os
import json
from typing import Optional, List
from datetime import datetime
import asyncio
import io
import re
import zipfile

print("----------------------------------Python version:", sys.version)
# Configuration du logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://import-icloud-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialisation du gestionnaire de sessions
session_manager = ImportSessionManager()

# Validation des entrées
def validate_email(email: str) -> bool:
    """Valide le format d'un email."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_password(password: str) -> bool:
    """Valide que le mot de passe respecte les critères de sécurité Apple."""
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    return True

def sanitize_path(path: str) -> str:
    """Nettoie le chemin pour éviter les injections."""
    return re.sub(r'[<>:"|?*]', '', path)

@app.get("/")
async def root():
    return {"message": "API iCloud Importer en ligne"}

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

class ImportRequest(BaseModel):
    email: str
    password: constr(min_length=8)
    destination_folder: str
    limit: Optional[int] = None

    def validate(self):
        if not validate_email(self.email):
            raise ValueError("Format d'email invalide")
        if not validate_password(self.password):
            raise ValueError("Le mot de passe ne respecte pas les critères de sécurité Apple")
        self.destination_folder = sanitize_path(self.destination_folder)
        if self.limit is not None and self.limit < 0:
            raise ValueError("La limite doit être positive")

class TwoFactorRequest(BaseModel):
    email: str
    password: constr(min_length=8)
    code: constr(min_length=6, max_length=6)
    destination_folder: str
    limit: Optional[int] = None
    session_id: str

    def validate(self):
        if not validate_email(self.email):
            raise ValueError("Format d'email invalide")
        if not validate_password(self.password):
            raise ValueError("Le mot de passe ne respecte pas les critères de sécurité Apple")
        self.destination_folder = sanitize_path(self.destination_folder)
        if self.limit is not None and self.limit < 0:
            raise ValueError("La limite doit être positive")
        if not re.match(r'^[a-f0-9-]{36}$', self.session_id):
            raise ValueError("ID de session invalide")

class StopRequest(BaseModel):
    session_id: str

    def validate(self):
        if not re.match(r'^[a-f0-9-]{36}$', self.session_id):
            raise ValueError("ID de session invalide")

# Stock temporaire des sessions iCloud en mémoire (exemple simple)
sessions = {}

@app.post("/start")
async def start_import(request: ImportRequest, background_tasks: BackgroundTasks):
    try:
        logger.info(f"Démarrage de l'import pour l'email: {request.email}")
        request.validate()
        logger.info("Validation des entrées réussie")
        
        session_id = str(uuid.uuid4())
        logger.info(f"Création de la session: {session_id}")
        
        session = ImportSession(
            email=request.email,
            password=request.password,
            destination=request.destination_folder,
            limit=request.limit,
            session_id=session_id
        )
        logger.info("Session créée")
        
        session_manager.add_session(session)
        logger.info("Session ajoutée au gestionnaire")
        
        background_tasks.add_task(run_import_session, session_id, session_manager)
        logger.info("Tâche d'import ajoutée aux tâches en arrière-plan")
        
        return {"session_id": session_id, "message": "Import démarré"}
    except ValueError as e:
        logger.error(f"Erreur de validation: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erreur lors du démarrage de l'import: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@app.post("/2fa")
async def submit_2fa(request: TwoFactorRequest, background_tasks: BackgroundTasks):
    try:
        request.validate()
        # Récupérer la session existante
        session = session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session non trouvée")
        # Vérifier si le code 2FA a déjà été validé pour cette session
        if hasattr(session, "_2fa_validated") and session._2fa_validated:
            return {"session_id": session.session_id, "message": "2FA déjà validé, import en cours"}
        # Utiliser la session iCloud existante (pyicloud gère le fichier de session)
        api = PyiCloudService(session.email, session.password)
        if not api.validate_2fa_code(request.code):
            raise HTTPException(status_code=400, detail="Code 2FA invalide")
        # Marquer la session comme validée pour le 2FA
        session._2fa_validated = True
        session.status = "running"
        session.save()
        background_tasks.add_task(run_import_session, session.session_id, session_manager)
        return {"session_id": session.session_id, "message": "Import démarré après 2FA"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erreur lors de la validation 2FA: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@app.post("/stop")
async def stop_import(request: StopRequest):
    try:
        request.validate()
        session = session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session non trouvée")
        session.stop()
        return {"message": "Import arrêté"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erreur lors de l'arrêt de l'import: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@app.get("/status/{session_id}")
async def get_status(session_id: str):
    try:
        logger.info(f"Vérification du statut pour la session: {session_id}")
        if not re.match(r'^[a-f0-9-]{36}$', session_id):
            raise HTTPException(status_code=400, detail="ID de session invalide")
        
        session = session_manager.get_session(session_id)
        if not session:
            logger.warning(f"Session non trouvée: {session_id}")
            raise HTTPException(status_code=404, detail="Session non trouvée")
        
        status = {
            "status": session.status,
            "progress": session.progress,
            "total": session.total,
            "errors": session.errors,
            "files_to_download": session.files_to_download
        }
        logger.info(f"Statut de la session {session_id}: {status}")
        return status
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@app.get("/download/{session_id}/{token}")
async def download_file(session_id: str, token: str):
    try:
        if not re.match(r'^[a-f0-9-]{36}$', session_id):
            raise HTTPException(status_code=400, detail="ID de session invalide")
        if not re.match(r'^[A-Za-z0-9+/]{43}=$', token):
            raise HTTPException(status_code=400, detail="Token invalide")
            
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session non trouvée")
        
        file_info = session.download_tokens.get(token)
        if not file_info:
            raise HTTPException(status_code=404, detail="Fichier non trouvé")
        
        if datetime.now() > file_info['expires']:
            raise HTTPException(status_code=410, detail="Lien de téléchargement expiré")
        
        return StreamingResponse(
            io.BytesIO(file_info['data']),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{file_info["filename"]}"'
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors du téléchargement: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

def zipfile_generator(session):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in session.files_to_download:
            token = file["token"]
            file_info = session.download_tokens.get(token)
            if file_info:
                zip_file.writestr(file["path"], file_info["data"])
    zip_buffer.seek(0)
    while True:
        chunk = zip_buffer.read(8192)
        if not chunk:
            break
        yield chunk

@app.get("/download-zip/{session_id}")
async def download_zip(session_id: str):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session non trouvée")
    if not session.files_to_download:
        raise HTTPException(status_code=404, detail="Aucun fichier à télécharger")
    if len(session.files_to_download) > 500:
        raise HTTPException(status_code=400, detail="Le téléchargement en .zip est limité à 500 fichiers à la fois. Veuillez importer par lots.")
    headers = {
        "Content-Disposition": f'attachment; filename="icloud_{session_id}.zip"',
        "Content-Type": "application/zip"
    }
    return StreamingResponse(
        zipfile_generator(session),
        media_type="application/zip",
        headers=headers
    )

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
