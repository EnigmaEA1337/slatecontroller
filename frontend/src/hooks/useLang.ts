/**
 * Sélecteur de langue d'interface — fr (par défaut) / en.
 *
 *  - Stocké en localStorage sous "slate-lang" pour persister entre
 *    sessions navigateur.
 *  - Diffusé via l'attribut HTML <html lang="..."> pour aider les
 *    lecteurs d'écran et les moteurs de recherche.
 *  - Hook minimal, sans dépendance i18next. Le système de traduction
 *    lui-même vit dans `@/lib/i18n` ; ce fichier ne gère que le choix.
 *
 *  Usage :
 *      const { lang, setLang } = useLang();
 *      setLang("fr" | "en");
 */

import { useCallback, useEffect, useState } from "react";

export type Lang = "fr" | "en";
export const DEFAULT_LANG: Lang = "fr";
export const SUPPORTED_LANGS: readonly Lang[] = ["fr", "en"] as const;

const STORAGE_KEY = "slate-lang";

function readStored(): Lang {
  if (typeof window === "undefined") return DEFAULT_LANG;
  const v = window.localStorage.getItem(STORAGE_KEY);
  if (v === "fr" || v === "en") return v;
  return DEFAULT_LANG;
}

function applyLang(l: Lang): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("lang", l);
}

// Mini-écouteur d'événements pour synchroniser plusieurs onglets et
// les composants qui lisent la langue sans passer par un Context. Le
// hook useT() s'y abonne pour re-rendre automatiquement.
type Listener = (l: Lang) => void;
const listeners = new Set<Listener>();

export function subscribeLang(cb: Listener): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function getLang(): Lang {
  return readStored();
}

export function setLangGlobal(l: Lang): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, l);
  }
  applyLang(l);
  for (const cb of listeners) cb(l);
}

export function useLang(): { lang: Lang; setLang: (l: Lang) => void } {
  const [lang, setLangState] = useState<Lang>(readStored);

  useEffect(() => {
    applyLang(lang);
  }, [lang]);

  // S'abonner aux changements globaux pour que tout composant qui
  // appelle useLang() reste synchronisé même si un autre composant
  // change la langue.
  useEffect(() => {
    return subscribeLang((l) => setLangState(l));
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    setLangGlobal(l);
  }, []);

  return { lang, setLang };
}

/** Initialisation eager — à appeler avant le mount React pour que le
 *  premier rendu connaisse déjà la langue active (sans flash de FR si
 *  l'utilisateur a choisi EN). */
export function initLangFromStorage(): void {
  applyLang(readStored());
}
