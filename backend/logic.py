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

# Configuration du logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

register_heif_opener()

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR, exist_ok=True)

SESSIONS_LOCK = threading.Lock()

def session_file_path(session_id):
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")

class ImportSession:
    def __init__(self, email, password, destination, limit, session_id=None, status="ready", progress=0, total=None, errors=None, imported_files=None):
        self.email = email  # Peut être gardé pour l'affichage, ou supprimé si tu veux une sécurité maximale
        self._password = password  # Ne jamais sauvegarder sur disque
        self.destination = destination
        self.limit = limit
        self.status = status  # ready, running, paused, finished, error
        self.progress = progress
        self.total = total if total is not None else (limit if limit else None)
        self.errors = errors if errors is not None else []
        self.thread = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # autorisé à tourner
        self._stop_event = threading.Event()
        self.session_id = session_id
        self.imported_files = set(imported_files) if imported_files else set()
        self.imported_log_path = os.path.join(destination, "imported_files.log")
        if os.path.exists(self.imported_log_path):
            with open(self.imported_log_path, "r", encoding="utf-8") as f:
                self.imported_files.update(line.strip() for line in f if line.strip())

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
            "email": self.email,  # Peut être retiré si tu veux
            # "password": self._password,  # NE PAS SAUVEGARDER
            "destination": self.destination,
            "limit": self.limit,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "errors": self.errors,
            "session_id": self.session_id,
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
        return ImportSession(
            email=data.get("email", ""),
            password=None,  # Le mot de passe doit être fourni à la reprise
            destination=data["destination"],
            limit=data["limit"],
            session_id=data.get("session_id"),
            status=data.get("status", "ready"),
            progress=data.get("progress", 0),
            total=data.get("total"),
            errors=data.get("errors", []),
            imported_files=data.get("imported_files", []),
        )

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
        # Création du dossier de destination avec gestion des erreurs
        try:
            if not os.path.exists(session.destination):
                logger.info(f"Création du dossier de destination: {session.destination}")
                os.makedirs(session.destination, exist_ok=True)
        except Exception as e:
            logger.error(f"Erreur lors de la création du dossier: {str(e)}")
            session.status = "error"
            session.errors.append(f"Impossible de créer le dossier de destination: {str(e)}")
            session.save()
            return

        logger.info(f"Tentative de connexion à iCloud pour {session.email}")
        api = PyiCloudService(session.email, session.password)
        if api.requires_2fa:
            logger.info("Authentification à deux facteurs requise.")
            session.status = "error"
            session.errors.append("Authentification à deux facteurs requise.")
            session.save()
            return

        if session.limit:
            photos_iter = api.photos.all  # On ne fait plus de islice ici
            session.total = session.limit
        else:
            photos_iter = api.photos.all
            session.total = None
        errors = []
        progress = tqdm(total=session.total, desc="Importation", unit="fichier") if session.total else tqdm(desc="Importation", unit="fichier")
        processed = 0
        retry_queue = []
        newly_imported = []
        batch = []

        for asset in photos_iter:
            if session.limit and processed >= session.limit:
                break
            # Vérifie la pause/stop AVANT de traiter chaque fichier
            while session.is_paused():
                logger.info(f"[THREAD] Import en pause pour session {session_id}...")
                time.sleep(0.5)
            if session.is_stopped():
                logger.info(f"[THREAD] Import stoppé pour session {session_id}.")
                session.status = "stopped"
                session.save()
                return
            filename = asset.filename or f"photo_{int(time.time() * 1000)}"
            ext = os.path.splitext(filename)[1].lower()
            # Récupération de la date de création
            date_obj = None
            if hasattr(asset, 'created') and asset.created:
                date_obj = asset.created
            elif hasattr(asset, 'creation_date') and asset.creation_date:
                date_obj = asset.creation_date
            else:
                date_obj = None
            if date_obj:
                year = str(date_obj.year)
                month = f"{date_obj.month:02d}"
                subfolder = os.path.join(session.destination, year, month)
            else:
                subfolder = session.destination
            if not os.path.exists(subfolder):
                os.makedirs(subfolder, exist_ok=True)
            # Vérifie si déjà importé (par nom de fichier)
            if filename in session.imported_files or (ext == ".heic" and filename.replace(".heic", ".jpg") in session.imported_files):
                logger.debug(f"Fichier déjà importé, on saute : {filename}")
                continue
            # On stocke aussi le sous-dossier cible dans l'asset pour le batch
            asset._import_subfolder = subfolder
            batch.append(asset)
            processed += 1
            if len(batch) >= batch_size or (session.limit and processed >= session.limit):
                _process_batch(batch, session, newly_imported, retry_queue, progress)
                batch = []
                session.progress = processed
                session.save()
        # Dernier batch
        if batch:
            _process_batch(batch, session, newly_imported, retry_queue, progress)
            session.progress = processed
            session.save()
        progress.close()
        # Ajout des nouveaux fichiers importés au log
        if newly_imported:
            with open(session.imported_log_path, "a", encoding="utf-8") as f:
                for fname in newly_imported:
                    f.write(fname + "\n")
            session.imported_files.update(newly_imported)
            session.save()
        # Deuxième passe : retry
        if retry_queue:
            logger.info(f"{len(retry_queue)} fichiers en erreur. Nouvelle tentative...")
            for asset in retry_queue:
                # Vérifie le flag d'arrêt dans la boucle de retry
                while session.is_paused():
                    logger.info(f"[THREAD] Import en pause (retry) pour session {session_id}...")
                    time.sleep(0.5)
                if session.is_stopped():
                    logger.info(f"[THREAD] Import stoppé (retry) pour session {session_id}.")
                    session.status = "stopped"
                    session.save()
                    return
                filename = asset.filename or f"photo_{int(time.time() * 1000)}"
                ext = os.path.splitext(filename)[1].lower()
                if filename in session.imported_files or (ext == ".heic" and filename.replace(".heic", ".jpg") in session.imported_files):
                    logger.debug(f"Fichier déjà importé (retry), on saute : {filename}")
                    continue
                try:
                    raw_data = asset.download().raw.read()
                    if ext == ".heic":
                        image = Image.open(io.BytesIO(raw_data))
                        filename_jpg = os.path.splitext(filename)[0] + ".jpg"
                        path = os.path.join(session.destination, filename_jpg)
                        image.save(path, format="JPEG")
                        newly_imported.append(filename_jpg)
                    else:
                        path = os.path.join(session.destination, filename)
                        with open(path, "wb") as f:
                            f.write(raw_data)
                        newly_imported.append(filename)
                except Exception as e:
                    logger.error(f"Erreur persistante pour {filename}: {str(e)}")
                    errors.append(f"{filename}: {str(e)}")
            session.save()
        # Ajout des nouveaux fichiers importés au log (retry)
        if newly_imported:
            with open(session.imported_log_path, "a", encoding="utf-8") as f:
                for fname in newly_imported:
                    f.write(fname + "\n")
            session.imported_files.update(newly_imported)
            session.save()
        # Écriture d'un log final si erreurs restantes
        if errors:
            log_path = os.path.join(session.destination, "import_errors.log")
            with open(log_path, "w", encoding="utf-8") as log_file:
                for err in errors:
                    log_file.write(err + "\n")
            logger.warning(f"{len(errors)} erreurs persistantes. Voir le log : {log_path}")
            session.status = "error"
            session.errors.extend(errors)
            session.save()
        else:
            logger.info("Importation complétée sans erreur.")
            session.status = "finished"
            session.save()
    except Exception as e:
        logger.error(f"Erreur globale: {str(e)}")
        session.status = "error"
        session.errors.append(f"Erreur lors de l'importation: {str(e)}")
        session.save()

def _process_batch(batch, session, newly_imported, retry_queue, progress):
    for asset in batch:
        # Vérifie la pause à chaque fichier du batch
        while session.is_paused():
            logger.info("Import en pause (batch)...")
            time.sleep(0.5)
        if session.is_stopped():
            logger.info("Import stoppé (batch).")
            session.status = "paused"
            session.save()
            return
        filename = asset.filename or f"photo_{int(time.time() * 1000)}"
        ext = os.path.splitext(filename)[1].lower()
        # Utilise le sous-dossier calculé précédemment
        subfolder = getattr(asset, '_import_subfolder', session.destination)
        try:
            logger.debug(f"Traitement du fichier: {filename}")
            raw_data = asset.download().raw.read()
            if ext == ".heic":
                image = Image.open(io.BytesIO(raw_data))
                filename_jpg = os.path.splitext(filename)[0] + ".jpg"
                path = os.path.join(subfolder, filename_jpg)
                image.save(path, format="JPEG")
                newly_imported.append(filename_jpg)
            else:
                path = os.path.join(subfolder, filename)
                with open(path, "wb") as f:
                    f.write(raw_data)
                newly_imported.append(filename)
            progress.update(1)
        except Exception as e:
            logger.error(f"Erreur lors de l'import de {filename}: {str(e)}")
            retry_queue.append(asset)

