// Product-fact hashtag generator. No filler, no invented attributes.
const fs = require("fs");
const defs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const gems = ["diamond", "sapphire", "ruby", "emerald", "tanzanite", "spinel", "tourmaline", "aquamarine", "opal", "pearl", "amethyst", "topaz"];
const colors = ["pink", "yellow", "green", "blue", "brown", "orange", "purple", "red", "grey", "black", "white"];
const shapes = ["pear", "round", "oval", "cushion", "radiant", "emerald", "asscher", "heart", "marquise", "princess"];
const typeRules = [[/\bearrings?\b/i, 1], [/\bpendants?\b/i, 2], [/\bbracelets?\b/i, 3], [/\bnecklaces?\b/i, 4], [/\brings?\b/i, 0], [/\bgemstones?\b/i, 5]];
const colorRe = new RegExp("(?:^|[^a-z])(" + colors.map(s => s.replace("grey", "gr[ae]y")).join("|") + ")(?:ish)?(?=$|[^a-z])", "gi");
const hash = s => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13; return h >>> 0; };
const clean = s => String(s).normalize("NFC").replace(/[^\p{L}\p{M}\p{N}_]/gu, "");
const fold = s => clean(s).toLocaleLowerCase();
const uniq = tags => [...new Map(tags.filter(Boolean).map(t => [fold(t), "#" + clean(t)])).values()];
const colorHits = text => {
  const hits = [];
  String(text || "").replace(colorRe, (full, name, offset) => {
    const key = name.toLowerCase().replace("gray", "grey");
    const i = colors.indexOf(key);
    if (i >= 0) hits.push({ i, offset });
    return full;
  });
  return hits;
};
const sets = {};
const output = [];
for (const row of input.rows) {
  const id = String(row.stock ?? row.id ?? "").trim();
  if (!id) continue;
  const jew = String(row.sheet).startsWith("jew");
  const e = String(row.details ?? "");
  const type = String(row.type ?? row.check ?? "");
  const lab = String(row.lab ?? "");
  const code = String(row.code ?? "");
  const cert = clean(String(row.cert ?? ""));
  const src = type + "\n" + e + "\n" + code;
  const conflict = /Sapphire[\s\S]*Ruby\s*:/i.test(e);
  let gem = "";
  if (!conflict) {
    if (!jew) gem = "diamond";
    else {
      const named = e.match(/\b(sapphire|ruby|emerald|tanzanite|spinel|tourmaline|aquamarine|opal|pearl|amethyst|topaz)\b/i);
      gem = named && !(/\bemerald\s*cut\b/i.test(e) && named[1].toLowerCase() === "emerald")
        ? named[1].toLowerCase()
        : (/\bdiamonds?\b/i.test(src) ? "diamond" : "");
    }
  }
  const weight = jew
    ? (e.match(/Main\s*stone\s*[-:–]?\s*\d+\s*(?:Pcs|pcs)\s*[-–]?\s*([\d.]+)\s*Ct/i)?.[1] || e.match(/([\d.]+)\s*cts?\b/i)?.[1] || "")
    : (e.match(/Weight\s*-\s*([\d.]+)/i)?.[1] || "");
  const detailColor = jew
    ? (e.match(/(?:Fancy|Very Light|Light|Vivid|Intense|Faint|Deep)[ \w-]*?(?:Pink|Yellow|Green|Blue|Brown|Orange|Purple|Red|Grey|Gray|Black)(?:ish)?(?:[ -]+(?:Pink|Yellow|Green|Blue|Brown|Orange|Purple|Red|Grey|Gray|Black)(?:ish)?)*/i)?.[0]
      || code
      || "")
    : (e.match(/Colour\s*-\s*([^\n\r]+)/i)?.[1] || code || "");
  const colorText = jew && colorHits(type).length ? type : detailColor;
  const hits = colorHits(colorText);
  const primary = hits.length ? hits[hits.length - 1].i : -1;
  const extraColors = [...new Set(hits.map(h => h.i).filter(i => i !== primary))];
  if (jew && /\bWD\b|Round\s+WHITE|\bWHITE\s+\d+\s*Pcs/i.test(e) && primary !== 10) extraColors.push(10);
  const letterGrade = !jew && /^[A-Z](?:\s*-\s*[A-Z])?$/.test(String(colorText).trim()) ? String(colorText).trim().replace(/\s*-\s*/g, "") : "";
  const clarity = gem === "diamond" ? (e.match(/\b(?:FL|IF|VVS[12]?|VS[12]?|SI[123]?|I[123])\b/i)?.[0]?.toUpperCase() || "") : "";
  const shape = !jew ? (e.match(/Shape\s*-\s*([^\n\r]+)/i)?.[1]?.trim() || "") : "";
  let tis = jew ? [...new Set(typeRules.filter(([re]) => re.test(type)).map(([, i]) => i))] : [6];
  if (!tis.length) tis = [7];
  const labTag = (lab.match(/\b(GIA|IGI|HRD|CGL|AGL)\b/i) || e.match(/\b(GIA|IGI|HRD|CGL|AGL)\b/i) || [])[0];
  const metal = /\b18\s*k\b/i.test(e) ? "18KGold" : (/\bplatinum\b/i.test(e) ? "Platinum" : "");
  const grams = (e.match(/([\d.]+)\s*grams?\b/i) || [])[1];
  const measure = [...e.matchAll(/(\d+(?:\.\d+)?)\s*[xX×-]\s*(\d+(?:\.\d+)?)(?:\s*[xX×-]\s*(\d+(?:\.\d+)?))?/g)][0]?.slice(1).filter(Boolean) || [];
  const fluor = (e.match(/Fluorescence\s*-\s*([^\n\r]+)/i) || [])[1];
  const polish = (e.match(/Polish\s*-\s*([A-Za-z]+)/i) || [])[1];
  const symmetry = (e.match(/Symmetry\s*-\s*([A-Za-z]+)/i) || [])[1];
  const result = { sheet: row.sheet, sheet_id: row.sheet_id, row: row.row, stock: id, cells: [] };
  for (const [cs, lang] of Object.entries(defs.locale_columns)) {
    const column = Number(cs), l = defs.locales[lang];
    if (!l) throw Error("missing locale " + lang);
    const facts = ["ColourDiam"];
    for (const i of tis) facts.push(l.types[i]);
    const gi = gems.indexOf(gem);
    if (gi >= 0) {
      const gemTag = l.gems[gi];
      if (!facts.some(t => fold(t) === fold(gemTag) || fold(t).includes(fold(gemTag)) || fold(gemTag).includes(fold(t)))) facts.push(gemTag);
    }
    if (primary >= 0) facts.push(l.colors[primary]);
    else if (letterGrade && lang === "en") facts.push(letterGrade + "ColourGrade");
    if (weight) facts.push(String(weight).replace(".", "_") + l.carat);
    const si = shapes.findIndex(x => x.toLowerCase() === shape.toLowerCase());
    if (si >= 0) facts.push((defs.shapes[lang] || defs.shapes.en)[si]);
    if (clarity) facts.push(clarity + l.clarity);
    if (labTag && lang === "en") facts.push(labTag.toUpperCase());
    if (metal && lang === "en") facts.push(metal);
    const extras = extraColors.map(i => l.colors[i]);
    if (grams) extras.push(String(grams).replace(".", "_") + "Gram");
    if (measure.length >= 2) extras.push(measure.map(n => String(n).replace(".", "_")).join("x"));
    if (fluor && !/^none$/i.test(fluor.trim())) extras.push(fluor.trim().replace(/[^A-Za-z0-9]+/g, "") + "Fluor");
    if (polish) extras.push(polish + "Polish");
    if (symmetry) extras.push(symmetry + "Sym");
    const pcs = (e.match(/Main\s*stone\s*[-:–]?\s*(\d+)\s*(?:Pcs|pcs)/i) || e.match(/No Of Diamonds\s*-\s*(\d+)/i) || [])[1];
    if (pcs && Number(pcs) > 1) extras.push(pcs + "Stone");
    sets[column] ??= new Set();
    let tags;
    const pools = column === 146
      ? [facts.concat(extras), ...extras.map((_, i) => facts.concat(extras.slice(0, extras.length - i - 1))), facts]
      : [facts, ...extras.map((_, i) => facts.concat(extras.slice(0, i + 1)))];
    for (const pool of pools) {
      const candidate = uniq(pool);
      const canonical = candidate.map(t => t.toLocaleLowerCase()).sort().join(" ");
      if (sets[column].has(canonical)) continue;
      sets[column].add(canonical);
      tags = candidate;
      break;
    }
    if (!tags) {
      const ident = "Item" + clean(id) + "r" + String(row.row);
      const candidate = uniq([...facts, ...extras, ident]);
      const canonical = candidate.map(t => t.toLocaleLowerCase()).sort().join(" ");
      if (sets[column].has(canonical)) throw Error("Could not create a distinct factual hashtag combination; no sheet changes.");
      sets[column].add(canonical);
      tags = candidate;
    }
    const seed = hash(id + ":" + lang + ":" + column + ":" + row.row + ":" + row.sheet);
    tags.sort((a, b) => hash(seed + ":" + a) - hash(seed + ":" + b));
    result.cells.push({ column, value: tags.join(" ") });
  }
  output.push(result);
}
process.stdout.write(JSON.stringify({ rows: output }));
