import { initializeApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider } from 'firebase/auth';
import { getAnalytics } from "firebase/analytics";

const firebaseConfig = {
    apiKey: "AIzaSyBooVZBfMDqS-2d2I88qWPJf3EcUgRq9u0",
    authDomain: "importicloud-4a8e8.firebaseapp.com",
    projectId: "importicloud-4a8e8",
    storageBucket: "importicloud-4a8e8.firebasestorage.app",
    messagingSenderId: "690473646119",
    appId: "1:690473646119:web:d290430c85e9e6ab913602",
    measurementId: "G-3NW3P3RE56"
  };

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
const analytics = getAnalytics(app);
export const googleProvider = new GoogleAuthProvider(); 