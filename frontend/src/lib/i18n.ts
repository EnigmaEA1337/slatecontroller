/**
 * Système de traduction maison — léger, typé, sans dépendance externe.
 *
 *  - Le dictionnaire vit dans `i18n-dict.ts` (un objet par langue).
 *  - La langue source est **français** : toute clé manquante en
 *    anglais retombe automatiquement sur la version FR pour ne jamais
 *    afficher la clé brute à l'utilisateur.
 *  - Le hook `useT()` renvoie une fonction `t(key, params?)` qui se
 *    re-rend automatiquement quand l'utilisateur change la langue.
 *  - Interpolation : `{nom}` dans la chaîne, remplacée par le paramètre.
 *
 *  Exemple :
 *      const t = useT();
 *      <h1>{t("pcap.title")}</h1>
 *      <p>{t("pcap.captured", { bytes: "12 MB" })}</p>
 */

import { useSyncExternalStore } from "react";

import {
  type Lang,
  getLang,
  subscribeLang,
} from "@/hooks/useLang";
import { DICT } from "./i18n-dict";

type Params = Record<string, string | number>;

function interpolate(s: string, params?: Params): string {
  if (!params) return s;
  let out = s;
  for (const [k, v] of Object.entries(params)) {
    out = out.split(`{${k}}`).join(String(v));
  }
  return out;
}

/** Résout une clé dotée comme "pcap.title" en parcourant l'objet. */
function lookup(lang: Lang, key: string): string | undefined {
  const parts = key.split(".");
  let cur: unknown = DICT[lang];
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return typeof cur === "string" ? cur : undefined;
}

/** Cœur du système : fonction de traduction pure, indépendante de React.
 *  Utilisable dans des modules non-React (ex. erreurs catch, helpers). */
export function translate(
  lang: Lang,
  key: string,
  params?: Params,
): string {
  const found = lookup(lang, key) ?? lookup("fr", key);
  if (found === undefined) {
    // En dev, log la clé manquante pour faciliter le repérage. En prod
    // on retourne la clé telle quelle — moche mais explicite, jamais
    // une string vide.
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(`[i18n] clé manquante : ${key} (lang=${lang})`);
    }
    return key;
  }
  return interpolate(found, params);
}

/** Hook React : renvoie une `t()` qui re-render au changement de langue.
 *  Utilise useSyncExternalStore pour s'abonner à `subscribeLang` sans
 *  passer par un Context Provider (le rendant utilisable n'importe où,
 *  y compris dans les composants hors arbre Provider). */
export function useT(): (key: string, params?: Params) => string {
  const lang = useSyncExternalStore(
    subscribeLang,
    getLang,
    () => "fr" as Lang,
  );
  return (key: string, params?: Params) => translate(lang, key, params);
}

/** Variante non-hook quand on a déjà la langue (ex. dans un callback). */
export { getLang } from "@/hooks/useLang";
