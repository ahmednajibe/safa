import { createContext, useContext } from "react";
import { ar } from "./ar";
import { en } from "./en";
import { formatMessage } from "./format";
import type { Dict } from "./en";

export type Lang = "en" | "ar";

export const DICTS: Record<Lang, Dict> = { en, ar };

export interface I18n {
  lang: Lang;
  dir: "ltr" | "rtl";
  d: Dict;
  /** Interpolate a dict template: t(d.progress.step, {n: 2, total: 5}). */
  t: (template: string, params?: Record<string, unknown>) => string;
  /** Localized backend message envelope; fallback = backend English. */
  msg: (
    code: string | null | undefined,
    params?: Record<string, unknown>,
    fallback?: string | null,
  ) => string;
  /** Western digits always (ar-u-nu-latn). */
  num: (n: number, opts?: Intl.NumberFormatOptions) => string;
}

const I18nContext = createContext<I18n>({
  lang: "en",
  dir: "ltr",
  d: en,
  t: (s) => s,
  msg: (_c, _p, fb) => fb ?? "",
  num: (n) => String(n),
});

export function makeI18n(lang: Lang): I18n {
  const dict = DICTS[lang];
  const nf = (opts?: Intl.NumberFormatOptions) =>
    new Intl.NumberFormat(lang === "ar" ? "ar-u-nu-latn" : "en-US", opts);
  return {
    lang,
    dir: lang === "ar" ? "rtl" : "ltr",
    d: dict,
    t: (template, params) =>
      template.replace(/\{(\w+)\}/g, (m, k: string) => {
        const v = params?.[k];
        return v === undefined || v === null ? m : String(v);
      }),
    msg: (code, params, fallback) =>
      formatMessage(code, params, dict, fallback, lang),
    num: (n, opts) => nf(opts).format(n),
  };
}

export const I18nProvider = I18nContext.Provider;

export function useT(): I18n {
  return useContext(I18nContext);
}
