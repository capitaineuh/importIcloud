import os
import time
from pyicloud import PyiCloudService
from tqdm import tqdm
from pillow_heif import register_heif_opener
from PIL import Image
import io
import logging
from itertools import islice
import threading
import json
import base64
from datetime import datetime, timedelta
from collections import defaultdict
import time
import traceback

# Configuration du logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

register_heif_opener()

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR, exist_ok=True)

SESSIONS_LOCK = threading.Lock()

# Protection contre les attaques par force brute
LOGIN_ATTEMPTS = defaultdict(list)
MAX_ATTEMPTS = 5
LOCKOUT_DURATION = 300  # 5 minutes en secondes

def check_login_attempts(email: str) -> bool:
    """Vérifie si l'utilisateur n'a pas dépassé le nombre maximum de tentatives."""
    now = time.time()
    attempts = LOGIN_ATTEMPTS[email]
    
    # Nettoyage des anciennes tentatives
    attempts = [t for t in attempts if now - t < LOCKOUT_DURATION]
    LOGIN_ATTEMPTS[email] = attempts
    
    if len(attempts) >= MAX_ATTEMPTS:
        return False
    return True

def record_login_attempt(email: str):
    """Enregistre une tentative de connexion."""
    LOGIN_ATTEMPTS[email].append(time.time())

def session_file_path(session_id):
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")

class ImportSession:
    def __init__(self, email, password, destination, limit, session_id=None, status="ready", progress=0, total=None, errors=None, imported_files=None):
        self.email = email
        self._password = password
        self.destination = destination
        self.limit = limit
        self.status = status
        self.progress = progress
        self.total = total if total is not None else (limit if limit else None)
        self.errors = errors if errors is not None else []
        self.thread = None
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self.session_id = session_id
        self.files_to_download = []  # Liste des fichiers à télécharger avec leurs URLs
        self.download_tokens = {}  # Stockage des tokens de téléchargement
        self.imported_files = set(imported_files) if imported_files else set()
        self.imported_log_path = os.path.join(destination, "imported_files.log")
        if os.path.exists(self.imported_log_path):
            with open(self.imported_log_path, "r", encoding="utf-8") as f:
                self.imported_files.update(line.strip() for line in f if line.strip())
        self.created_at = datetime.now()

    @property
    def password(self):
        return self._password

    @password.setter
    def password(self, value):
        self._password = value

    def pause(self):
        self.status = "paused"
        self._pause_event.clear()
        self.save()

    def resume(self):
        self.status = "running"
        self._pause_event.set()
        self.save()

    def stop(self):
        self._stop_event.set()
        self.save()

    def is_paused(self):
        return not self._pause_event.is_set()

    def is_stopped(self):
        return self._stop_event.is_set()

    def to_dict(self):
        return {
            "email": self.email,
            "destination": self.destination,
            "limit": self.limit,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "errors": self.errors,
            "session_id": self.session_id,
            "files_to_download": self.files_to_download,
            "imported_files": list(self.imported_files),
        }

    def save(self):
        if self.session_id:
            with open(session_file_path(self.session_id), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(session_id):
        path = session_file_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        session = ImportSession(
            email=data.get("email", ""),
            password=None,
            destination=data["destination"],
            limit=data["limit"],
            session_id=data.get("session_id"),
            status=data.get("status", "ready"),
            progress=data.get("progress", 0),
            total=data.get("total"),
            errors=data.get("errors", []),
            imported_files=data.get("imported_files", []),
        )
        session.files_to_download = data.get("files_to_download", [])
        return session

class ImportSessionManager:
    def __init__(self):
        self.sessions = {}
        self.load_all_sessions()

    def create_session(self, session_id, email, password, destination, limit):
        with SESSIONS_LOCK:
            session = ImportSession(email, password, destination, limit, session_id=session_id)
            self.sessions[session_id] = session
            session.save()

    def start(self, session_id):
        with SESSIONS_LOCK:
            session = self.sessions[session_id]
            session.status = "running"
            session.save()
            t = threading.Thread(target=run_import_session, args=(session_id, self))
            session.thread = t
            t.start()

    def pause(self, session_id):
        with SESSIONS_LOCK:
            session = self.sessions[session_id]
            logger.info(f"[PAUSE] Demande de pause pour session {session_id}")
            session.pause()

    def resume(self, session_id):
        with SESSIONS_LOCK:
            session = self.sessions[session_id]
            session.resume()
            if not session.thread or not session.thread.is_alive():
                t = threading.Thread(target=run_import_session, args=(session_id, self))
                session.thread = t
                t.start()

    def status(self, session_id):
        with SESSIONS_LOCK:
            session = self.sessions[session_id]
            return {
                "status": session.status,
                "progress": session.progress,
                "total": session.total,
                "errors": session.errors,
            }

    def load_all_sessions(self):
        for fname in os.listdir(SESSIONS_DIR):
            if fname.endswith(".json"):
                session_id = fname[:-5]
                session = ImportSession.load(session_id)
                if session:
                    self.sessions[session_id] = session

# Nouvelle fonction d'import pilotable par session

def run_import_session(session_id, session_manager, batch_size=10):
    with SESSIONS_LOCK:
        session = session_manager.sessions[session_id]
    logger.info(f"[THREAD] Démarrage de l'import pour session {session_id}")
    try:
        # Vérification de la sécurité
        if not check_login_attempts(session.email):
            logger.warning(f"Trop de tentatives de connexion pour {session.email}")
            session.status = "error"
            session.errors.append("Trop de tentatives de connexion. Veuillez réessayer dans 5 minutes.")
            session.save()
            return

        logger.info(f"Tentative de connexion à iCloud pour {session.email}")
        try:
            logger.info("Initialisation de l'API iCloud...")
            api = PyiCloudService(session.email, session.password)
            logger.info("Connexion à iCloud réussie")
            
            if api.requires_2fa:
                logger.info("Authentification à deux facteurs requise.")
                session.status = "error"
                session.errors.append("Authentification à deux facteurs requise.")
                session.save()
                return
        except Exception as e:
            logger.error(f"Erreur lors de la connexion à iCloud: {str(e)}")
            record_login_attempt(session.email)
            raise e

        logger.info("Récupération de la liste des photos...")
        photos_iter = api.photos.all
        session.total = session.limit if session.limit else len(photos_iter)
        logger.info(f"Nombre total de photos à traiter: {session.total}")
        
        processed = 0
        errors = []

        for asset in photos_iter:
            if session.limit and processed >= session.limit:
                logger.info(f"Limite atteinte ({session.limit} fichiers)")
                break

            while session.is_paused():
                logger.info(f"[THREAD] Import en pause pour session {session_id}...")
                time.sleep(0.5)
            if session.is_stopped():
                logger.info(f"[THREAD] Import stoppé pour session {session_id}.")
                session.status = "stopped"
                session.save()
                return

            try:
                filename = asset.filename or f"photo_{int(time.time() * 1000)}"
                logger.info(f"Traitement du fichier: {filename}")
                
                ext = os.path.splitext(filename)[1].lower()
                
                # Récupération de la date de création
                date_obj = None
                if hasattr(asset, 'created') and asset.created:
                    date_obj = asset.created
                elif hasattr(asset, 'creation_date') and asset.creation_date:
                    date_obj = asset.creation_date

                # Création du chemin relatif
                if date_obj:
                    year = str(date_obj.year)
                    month = f"{date_obj.month:02d}"
                    relative_path = f"{year}/{month}/{filename}"
                else:
                    relative_path = filename

                logger.info(f"Téléchargement du fichier: {relative_path}")
                download = asset.download()
                file_data = download.raw.read()
                logger.info(f"Fichier téléchargé: {len(file_data)} octets")

                # Conversion HEIC en JPG si nécessaire
                if ext == ".heic":
                    logger.info("Conversion HEIC en JPG...")
                    image = Image.open(io.BytesIO(file_data))
                    filename_jpg = os.path.splitext(filename)[0] + ".jpg"
                    relative_path = os.path.splitext(relative_path)[0] + ".jpg"
                    output = io.BytesIO()
                    image.save(output, format="JPEG")
                    file_data = output.getvalue()
                    logger.info("Conversion terminée")

                # Génération d'un token unique pour ce fichier
                token = base64.b64encode(os.urandom(32)).decode('utf-8')
                session.download_tokens[token] = {
                    'data': file_data,
                    'filename': os.path.basename(relative_path),
                    'expires': datetime.now() + timedelta(hours=24)
                }

                # Ajout du fichier à la liste des téléchargements
                session.files_to_download.append({
                    'path': relative_path,
                    'token': token,
                    'size': len(file_data)
                })

                processed += 1
                session.progress = processed
                session.save()
                logger.info(f"Progression: {processed}/{session.total}")

            except Exception as e:
                logger.error(f"Erreur lors du traitement de {filename}: {str(e)}")
                errors.append(f"{filename}: {str(e)}")

        if errors:
            logger.warning(f"Import terminé avec {len(errors)} erreurs")
            session.status = "error"
            session.errors.extend(errors)
        else:
            logger.info("Import terminé avec succès")
            session.status = "finished"
        session.save()

    except Exception as e:
        logger.error(f"Erreur globale: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        session.status = "error"
        session.errors.append(f"Erreur lors de l'importation: {str(e)}")
        session.save()

