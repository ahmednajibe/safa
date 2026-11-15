/** formatMessage: backend message envelopes (code + params) → localized
 * text. Unknown codes fall back to the backend's English `message`.
 * Arabic {count} messages carry Intl.PluralRules('ar') form objects
 * (UI_DESIGN §12). */

import type { Dict, MsgVal, PluralForms } from "./en";

type Params = Record<string, unknown> | undefined;

function interp(template: string, params: Params): string {
  return template.replace(/\{(\w+)\}/g, (m, key: string) => {
    const v = params?.[key];
    return v === undefined || v === null ? m : String(v);
  });
}

function pluralCategory(lang: string, n: number): string {
  return new Intl.PluralRules(lang === "ar" ? "ar" : "en").select(n);
}

function renderPlural(def: PluralForms, params: Params, lang: string): string {
  const raw = params?.[def.count];
  const n = typeof raw === "number" ? raw : Number(raw);
  const cat = Number.isFinite(n) ? pluralCategory(lang, n) : "other";
  const forms = def.forms as Record<string, string | undefined>;
  const tpl = forms[cat] ?? forms.other ?? "";
  return interp(tpl, { ...(params ?? {}), n: raw });
}

export function formatMessage(
  code: string | null | undefined,
  params: Params,
  dict: Dict,
  fallback?: string | null,
  lang: string = "en",
): string {
  const val: MsgVal | undefined = code ? dict.msg[code] : undefined;
  if (val === undefined) return fallback ?? "";
  if (typeof val === "string") return interp(val, params);
  if ("text" in val) {
    const subs: Record<string, unknown> = {};
    if (val.plurals) {
      for (const [name, def] of Object.entries(val.plurals)) {
        subs[name] = renderPlural(def, params, lang);
      }
    }
    return interp(val.text, { ...(params ?? {}), ...subs });
  }
  return fallback ?? "";
}
