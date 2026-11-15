/** formatMessage: backend message envelopes (code + params) → localized
 * text. Unknown codes fall back to the backend's English `message`.
 * Arabic {count} messages carry Intl.PluralRules('ar') form objects
 * (UI_DESIGN §12). */

import type { Dict, MsgVal, PluralForms } from "./en";

type Params = Record<string, unknown> | undefined;

function num(n: number, lang: string): string {
  return new Intl.NumberFormat(
    lang === "ar" ? "ar-u-nu-latn" : "en-US",
  ).format(n);
}

/** "281, 516, and 621" — items pre-formatted (numbers → Western digits even
 *  in Arabic); the ListFormat locale supplies separators/conjunction. */
function list(items: unknown[], lang: string): string {
  const fmt = new Intl.ListFormat(lang === "ar" ? "ar" : "en", {
    style: "long",
    type: "conjunction",
  });
  return fmt.format(
    items.map((x) => (typeof x === "number" ? num(x, lang) : String(x))),
  );
}

function interp(template: string, params: Params, lang: string): string {
  return template.replace(/\{(\w+)\}/g, (m, key: string) => {
    const v = params?.[key];
    if (v === undefined || v === null) return m;
    if (Array.isArray(v)) return list(v, lang);
    return typeof v === "number" ? num(v, lang) : String(v);
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
  return interp(tpl, { ...(params ?? {}), n: raw }, lang);
}

/** A plural whose count param resolves numerically to 0 — used to pick a
 *  message's `zero` template ("No breaths found" rather than "Made 0
 *  breaths quieter…"). */
function hasZeroCount(plurals: Record<string, PluralForms>, params: Params): boolean {
  for (const def of Object.values(plurals)) {
    const raw = params?.[def.count];
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(n) && n === 0) return true;
  }
  return false;
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
  if (typeof val === "string") return interp(val, params, lang);
  if ("text" in val) {
    const subs: Record<string, unknown> = {};
    if (val.plurals) {
      for (const [name, def] of Object.entries(val.plurals)) {
        subs[name] = renderPlural(def, params, lang);
      }
    }
    const template =
      val.zero !== undefined && val.plurals && hasZeroCount(val.plurals, params)
        ? val.zero
        : val.text;
    return interp(template, { ...(params ?? {}), ...subs }, lang);
  }
  return fallback ?? "";
}
