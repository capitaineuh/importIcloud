import { useState, useRef, useEffect } from "react";
import { auth } from './firebase';
import { signInWithEmailAndPassword, signOut, onAuthStateChanged, signInWithPopup } from 'firebase/auth';
import { googleProvider } from './firebase';

// Configuration de l'API
const API_URL = "https://import-icloud-backend-production.up.railway.app"; //import.meta.env.VITE_API_URL ||

// Fonction utilitaire pour obtenir le token Firebase
async function getFirebaseToken() {
  const user = auth.currentUser;
  if (!user) return null;
  return await user.getIdToken();
}

const styles = {
  page: {
    minHeight: '100vh',
    background: '#fff',
    fontFamily: 'SF Pro Display, Helvetica Neue, Arial, sans-serif',
    color: '#222',
    margin: 0,
    padding: 0,
  },
  container: {
    maxWidth: 480,
    margin: '40px auto',
    background: '#fff',
    borderRadius: 18,
    boxShadow: '0 4px 24px 0 rgba(0,0,0,0.04)',
    padding: 36,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
  },
  logo: {
    width: 48,
    height: 48,
    marginBottom: 18,
  },
  title: {
    fontWeight: 600,
    fontSize: 28,
    marginBottom: 18,
    letterSpacing: -1,
  },
  input: {
    width: 320,
    padding: '12px 16px',
    border: '1px solid #e0e0e0',
    borderRadius: 10,
    fontSize: 16,
    marginBottom: 18,
    outline: 'none',
    background: '#fafbfc',
    transition: 'border 0.2s',
  },
  inputFocus: {
    border: '1.5px solid #0071e3',
    background: '#fff',
  },
  button: {
    padding: '12px 28px',
    borderRadius: 22,
    border: 'none',
    fontWeight: 500,
    fontSize: 16,
    background: '#0071e3',
    color: '#fff',
    margin: '0 8px 0 0',
    cursor: 'pointer',
    boxShadow: '0 2px 8px 0 rgba(0,0,0,0.04)',
    transition: 'background 0.2s',
  },
  buttonAlt: {
    background: '#f5f5f7',
    color: '#222',
    border: '1px solid #e0e0e0',
  },
  buttonDanger: {
    background: '#ff3b30',
    color: '#fff',
  },
  status: {
    marginTop: 18,
    fontSize: 15,
    color: '#888',
    minHeight: 24,
    textAlign: 'center',
  },
  progressBarWrap: {
    width: 320,
    background: '#f5f5f7',
    borderRadius: 12,
    height: 18,
    marginTop: 24,
    overflow: 'hidden',
    boxShadow: '0 1px 4px 0 rgba(0,0,0,0.03)',
  },
  progressBar: percent => ({
    width: percent + '%',
    background: percent === 100 ? '#34c759' : '#0071e3',
    height: '100%',
    transition: 'width 0.5s',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#fff',
    fontWeight: 600,
    fontSize: 14,
    letterSpacing: 1,
  }),
  errorList: {
    color: '#ff3b30',
    marginTop: 12,
    fontSize: 14,
    textAlign: 'left',
  },
  userBar: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 24,
  },
  googleBtn: {
    ...this?.button,
    background: '#fff',
    color: '#222',
    border: '1.5px solid #e0e0e0',
    boxShadow: '0 2px 8px 0 rgba(0,0,0,0.04)',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontWeight: 500,
    fontSize: 16,
    padding: '12px 28px',
  },
};

function App() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [destination, setDestination] = useState("");
  const [limit, setLimit] = useState(null); // pour test 50, 500, 5000 fichiers
  const [status, setStatus] = useState("");
  const [step, setStep] = useState("login"); // "login" ou "2fa"
  const [code2fa, setCode2fa] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [importStatus, setImportStatus] = useState(null); // {status, progress, total, errors}
  const [polling, setPolling] = useState(false);
  const [downloadedFiles, setDownloadedFiles] = useState([]);
  const abortControllerRef = useRef(null);
  const pollingIntervalRef = useRef(null);
  const [user, setUser] = useState(null);
  const [isImporting, setIsImporting] = useState(false);

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, setUser);
    return () => unsub();
  }, []);

  // Fonction pour démarrer l'import sans 2FA
  const startImport = async (customLimit = null) => {
    if (isImporting) return; // Empêche le double appel
    setIsImporting(true);
    setStatus("Connexion en cours...");
    setLimit(customLimit);
    setSessionId(null);
    setImportStatus(null);
    abortControllerRef.current = new AbortController();
    try {
      const token = await getFirebaseToken();
      const safeDestination = destination.replace(/\\/g, "/");
      const response = await fetch(`${API_URL}/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          ...(token ? { "Authorization": `Bearer ${token}` } : {})
        },
        credentials: "include",
        signal: abortControllerRef.current.signal,
        body: JSON.stringify({
          email,
          password,
          destination_folder: safeDestination,
          limit: customLimit !== null ? parseInt(customLimit) : undefined,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        setStatus(`Erreur: ${errorData.detail || errorData.message}`);
        setIsImporting(false);
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      if (data.session_id) {
        setSessionId(data.session_id);
        setStatus("Import lancé. Suivi en cours...");
        setPolling(true);
      }
      if (data.message === "2FA required") {
        setStep("2fa");
        setStatus("Code 2FA requis, veuillez saisir le code envoyé.");
      } else if (!data.session_id) {
        setStatus(data.message);
      }
    } catch (error) {
      if (error.name === "AbortError") {
        setStatus("Importation interrompue par l'utilisateur.");
      } else {
        console.error("Erreur:", error);
        setStatus(`Erreur lors de la connexion au serveur: ${error.message}`);
      }
    } finally {
      setIsImporting(false);
    }
  };

  // Fonction pour valider le code 2FA
  const submit2fa = async () => {
    setStatus("Validation du code 2FA...");
    abortControllerRef.current = new AbortController();
    try {
      const token = await getFirebaseToken();
      const response = await fetch(`${API_URL}/2fa`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          ...(token ? { "Authorization": `Bearer ${token}` } : {})
        },
        credentials: "include",
        signal: abortControllerRef.current.signal,
        body: JSON.stringify({
          email,
          password,
          code: code2fa,
          destination_folder: destination,
          limit: limit !== null ? parseInt(limit) : undefined,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      if (data.session_id) {
        setSessionId(data.session_id);
        setStatus("Import lancé après 2FA. Suivi en cours...");
        setPolling(true);
        setStep("login");
        setCode2fa("");
      } else {
        setStatus(`Erreur 2FA : ${data.detail || data.message}`);
      }
    } catch (error) {
      if (error.name === "AbortError") {
        setStatus("Importation interrompue par l'utilisateur.");
      } else {
        console.error("Erreur:", error);
        setStatus(`Erreur lors de la validation du code 2FA: ${error.message}`);
      }
    }
  };

  // Gestion des boutons d'import
  const handleTest = () => {
    startImport(50);
  };
  const handle500 = () => {
    startImport(500);
  };
  const handle5000 = () => {
    startImport(5000);
  };
  const handleFullImport = () => {
    startImport(null);
  };

  // Bouton stop
  const handleStop = async () => {
    if (!sessionId || (importStatus && importStatus.status === 'stopped')) {
      return;
    }
    const token = await getFirebaseToken();
    await fetch(`${API_URL}/stop`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "Authorization": `Bearer ${token}` } : {})
      },
      body: JSON.stringify({ session_id: sessionId }),
    });
    setStatus("Import stoppé.");
    setPolling(false);
  };

  // Fonction pour télécharger un fichier
  const downloadFile = async (token, filename) => {
    try {
      const response = await fetch(`${API_URL}/download/${sessionId}/${token}`, {
        headers: {
          ...(token ? { "Authorization": `Bearer ${token}` } : {})
        }
      });
      
      if (!response.ok) {
        throw new Error(`Erreur lors du téléchargement: ${response.statusText}`);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error("Erreur lors du téléchargement:", error);
      setStatus(`Erreur lors du téléchargement: ${error.message}`);
    }
  };

  // Mise à jour du polling pour récupérer la liste des fichiers
  useEffect(() => {
    if (polling && sessionId) {
      pollingIntervalRef.current = setInterval(async () => {
        try {
          const token = await getFirebaseToken();
          const res = await fetch(`${API_URL}/status/${sessionId}`, {
            headers: {
              ...(token ? { "Authorization": `Bearer ${token}` } : {})
            }
          });
          if (res.ok) {
            const data = await res.json();
            setImportStatus(data);
            if (data.files_to_download) {
              setDownloadedFiles(data.files_to_download);
            }
            if (data.status === "finished" || data.status === "error") {
              setPolling(false);
              setStatus(data.status === "finished" ? "Import terminé !" : "Erreur lors de l'import.");
            }
          } else if (res.status === 404) {
            setPolling(false);
            setStatus("Session d'import non trouvée ou expirée");
          }
        } catch (e) {
          console.error("Erreur lors du polling:", e);
        }
      }, 2000);
      return () => clearInterval(pollingIntervalRef.current);
    }
  }, [polling, sessionId]);

  // Calcul de la progression (pour la barre)
  let percent = 0;
  if (importStatus && importStatus.total) {
    percent = Math.round((importStatus.progress / importStatus.total) * 100);
  }

  // Connexion Google
  const handleGoogleLogin = async () => {
    try {
      await signInWithPopup(auth, googleProvider);
    } catch (err) {
      alert('Erreur de connexion Google : ' + err.message);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    try {
      await signInWithEmailAndPassword(auth, email, password);
    } catch (err) {
      alert('Erreur de connexion : ' + err.message);
    }
  };

  const handleLogout = () => signOut(auth);

  // Affichage principal
  return (
    <div style={styles.page}>
      <div style={styles.container}>
        {/* Logo Apple-like (nuage) */}
        <svg style={styles.logo} viewBox="0 0 48 48" fill="none"><ellipse cx="24" cy="28" rx="16" ry="12" fill="#e0e0e0"/><ellipse cx="32" cy="20" rx="8" ry="6" fill="#f5f5f7"/><ellipse cx="18" cy="18" rx="10" ry="8" fill="#f5f5f7"/></svg>
        <div style={styles.title}>iCloud Importer</div>

        {!user ? (
          <button onClick={handleGoogleLogin} style={styles.googleBtn}>
            <svg width="20" height="20" viewBox="0 0 48 48"><g><circle fill="#fff" cx="24" cy="24" r="24"/><path fill="#4285F4" d="M34.6 24.2c0-.7-.1-1.4-.2-2H24v4.1h6c-.3 1.5-1.5 2.7-3.1 3.2v2.7h5c2.9-2.7 4.7-6.7 4.7-11z"/><path fill="#34A853" d="M24 36c2.7 0 5-0.9 6.7-2.4l-5-2.7c-1.4.9-3.2 1.4-5.1 1.4-3.9 0-7.2-2.6-8.4-6.1h-5v2.8C9.7 32.9 16.3 36 24 36z"/><path fill="#FBBC05" d="M15.6 26.2c-.3-.9-.5-1.8-.5-2.7s.2-1.8.5-2.7v-2.8h-5C8.7 20.7 8 22.3 8 24s.7 3.3 1.6 4.7l6-2.5z"/><path fill="#EA4335" d="M24 17.9c1.5 0 2.8.5 3.8 1.4l2.8-2.8C28.9 14.7 26.7 14 24 14c-7.7 0-14.3 3.1-16.4 7.6l6 2.8c1.2-3.5 4.5-6.1 8.4-6.1z"/></g></svg>
            Se connecter avec Google
          </button>
        ) : (
          <>
            <div style={styles.userBar}>
              <span style={{ fontSize: 15, color: '#888' }}>Connecté en tant que <b>{user.email}</b></span>
              <button onClick={handleLogout} style={{ ...styles.button, ...styles.buttonAlt }}>Déconnexion</button>
            </div>
            {step === "login" && (
              <>
                <input
                  type="email"
                  placeholder="Adresse iCloud"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  style={styles.input}
                />
                <input
                  type="password"
                  placeholder="Mot de passe"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={styles.input}
                />
                <input
                  type="text"
                  placeholder="Dossier de destination"
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                  style={styles.input}
                />
                <div style={{ display: 'flex', flexWrap: 'wrap', marginBottom: 18, gap: '12px' }}>
                  <button onClick={handleFullImport} style={styles.button}>Démarrer l'import complet</button>
                  <button onClick={handleTest} style={{ ...styles.button, ...styles.buttonAlt }}>Importer 50 fichiers</button>
                  <button onClick={handle500} style={{ ...styles.button, ...styles.buttonAlt }}>Importer 500 fichiers</button>
                  <button onClick={handle5000} style={{ ...styles.button, ...styles.buttonAlt }}>Importer 5000 fichiers</button>
                  <button
                    onClick={handleStop}
                    style={{ ...styles.button, ...styles.buttonDanger, marginLeft: '12px' }}
                    disabled={!sessionId || (importStatus && importStatus.status === 'stopped') || (importStatus && importStatus.status !== 'running')}
                  >
                    Stop
                  </button>
                </div>
              </>
            )}
            {step === "2fa" && (
              <>
                <p style={{ marginBottom: 18, color: '#666' }}>Entrez le code de vérification envoyé à votre appareil Apple :</p>
                <input
                  type="text"
                  placeholder="Code de vérification"
                  value={code2fa}
                  onChange={(e) => setCode2fa(e.target.value)}
                  style={styles.input}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: '12px' }}>
                  <button onClick={submit2fa} style={styles.button}>Valider le code</button>
                  <button 
                    onClick={() => {
                      setStep("login");
                      setCode2fa("");
                      setStatus("");
                    }} 
                    style={{ ...styles.button, ...styles.buttonAlt }}
                  >
                    Retour
                  </button>
                </div>
              </>
            )}
            {/* Barre de progression */}
            {importStatus && importStatus.total && (
              <div style={styles.progressBarWrap}>
                <div style={styles.progressBar(percent)}>
                  {percent}%
                </div>
              </div>
            )}
            {importStatus && importStatus.total && (
              <div style={{ marginTop: 10, fontSize: 15, color: '#888', textAlign: 'center' }}>
                {importStatus.progress} / {importStatus.total} fichiers
                {importStatus.status && <span style={{ marginLeft: 12 }}>Statut : {importStatus.status}</span>}
              </div>
            )}
            {importStatus && importStatus.errors && importStatus.errors.length > 0 && (
              <div style={styles.errorList}>
                Erreurs :
                <ul>
                  {importStatus.errors.map((err, idx) => (
                    <li key={idx}>{err}</li>
                  ))}
                </ul>
              </div>
            )}
            <div style={styles.status}>{status}</div>

            {downloadedFiles.length > 0 && (
              <div style={{ marginTop: 24, width: '100%' }}>
                <h3 style={{ fontSize: 18, marginBottom: 12 }}>Fichiers disponibles</h3>
                <div style={{ 
                  maxHeight: 300, 
                  overflowY: 'auto',
                  border: '1px solid #e0e0e0',
                  borderRadius: 10,
                  padding: 12
                }}>
                  {downloadedFiles.map((file, index) => (
                    <div key={index} style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '8px 0',
                      borderBottom: index < downloadedFiles.length - 1 ? '1px solid #e0e0e0' : 'none'
                    }}>
                      <span style={{ fontSize: 14 }}>{file.path}</span>
                      <button
                        onClick={() => downloadFile(file.token, file.path.split('/').pop())}
                        style={{
                          ...styles.button,
                          padding: '6px 12px',
                          fontSize: 14
                        }}
                      >
                        Télécharger
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default App;
