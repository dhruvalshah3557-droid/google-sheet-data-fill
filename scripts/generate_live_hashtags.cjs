// Generic marketing vocabulary and an in-memory hashtag generator.
// No product records, descriptions, prices or certificates are embedded in this file.
const fs = require("fs");
const defs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const gems=["diamond","sapphire","ruby","emerald","tanzanite","spinel","tourmaline","aquamarine","opal","pearl","amethyst","topaz"];
const colors=["pink","yellow","green","blue","brown","orange","purple","red","grey","black","white"];
const shapes=["pear","round","oval","cushion","radiant","emerald","asscher","heart","marquise","princess"];
const hash=s=>{let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);h^=h>>>16;h=Math.imul(h,0x85ebca6b);h^=h>>>13;return h>>>0;};
const clean=s=>String(s).normalize("NFC").replace(/[^\p{L}\p{M}\p{N}_]/gu,"");
const sets={},output=[];
for (const row of input.rows) {
 const id=String(row.stock??row.id??"").trim(); if (!id) continue;
 const jew=String(row.sheet).startsWith("jew");
 const e=String(row.details??""),type=String(row.type??"");
 const conflict=/Sapphire[\s\S]*Ruby\s*:/i.test(e);
 const gem=conflict?"":jew?(e.match(/\b(sapphire|ruby|emerald|tanzanite|spinel|tourmaline|aquamarine|opal|pearl|amethyst|topaz)\b/i)?.[1]?.toLowerCase()||(/\bdiamonds?\b/i.test(e)?"diamond":"")):"diamond";
 const weight=jew?(e.match(/Main\s*stone\s*[-:–]?\s*\d+\s*(?:Pcs|pcs)\s*[-–]?\s*([\d.]+)\s*Ct/i)?.[1]||e.match(/([\d.]+)\s*cts?\b/i)?.[1]||""):(e.match(/Weight\s*-\s*([\d.]+)/i)?.[1]||"");
 const color=jew?(e.match(/(?:Fancy|Very Light|Light|Vivid|Intense|Faint)[ \w-]*?(?:Pink|Yellow|Green|Blue|Brown|Orange|Purple|Red|Grey|Gray|Black)(?:ish)?(?:[ -]+(?:Pink|Yellow|Green|Blue|Brown|Orange|Purple|Red|Grey|Gray|Black)(?:ish)?)*/i)?.[0]||""):(e.match(/Colour\s*-\s*([^\n\r]+)/i)?.[1]||"");
 const clarity=gem==="diamond"?[...new Set(e.match(/\b(?:FL|IF|VVS[12]?|VS[12]?|SI[123]?|I[123])\b/ig)||[])].map(s=>s.toUpperCase()):[];
 const shape=!jew?(e.match(/Shape\s*-\s*([^\n\r]+)/i)?.[1]?.trim()||""):"";
 let tis=jew?[...new Set([[/\brings?\b/i,0],[/\bearrings?\b/i,1],[/\bpendants?\b/i,2],[/\bbracelets?\b/i,3],[/\bnecklaces?\b/i,4],[/\bgemstones?\b/i,5]].filter(([r])=>r.test(type)).map(([,i])=>i))]:[6];
 if(!tis.length)tis=[7];
 const result={sheet:row.sheet,sheet_id:row.sheet_id,row:row.row,stock:id,cells:[]};
 for (const [cs,lang] of Object.entries(defs.locale_columns)) {
  const column=Number(cs),l=defs.locales[lang],h=hash(id+":"+lang+":"+column+":"+row.row+":"+row.sheet);
  const base=["ColourDiam",...tis.map(i=>l.types[i])],gi=gems.indexOf(gem);
  if(gi>=0)base.push(l.gems[gi]);
  const found=colors.flatMap((s,i)=>new RegExp(s.replace("grey","gr[ae]y"),"i").test(color)?[i]:[]);
  if(found.length)base.push(...found.map(i=>l.colors[i]));else if(color&&lang==="en")base.push(color+"ColourGrade");
  if(weight)base.push(weight.replace(".","_")+l.carat);
  const si=shapes.findIndex(x=>x.toLowerCase()===shape.toLowerCase());
  if(si>=0)base.push((defs.shapes[lang]||defs.shapes.en)[si]);
  for(const cl of clarity.slice(0,2))base.push(cl+l.clarity);
  if(lang==="en"&&color&&found.length)base.push(color);
  const factual=[...new Map(base.map(t=>["#"+clean(t).toLocaleLowerCase(),"#"+clean(t)])).values()];
  sets[column]??=new Set(); let tags;
  for(let attempt=0;attempt<1000;attempt++){
   const seed=hash(h+":"+attempt);
   const choices=l.editor.map((_,i)=>i).sort((a,b)=>hash(seed+":"+a)-hash(seed+":"+b));
   const extra=choices.slice(0,2+((h+attempt)%3)).map(i=>"#"+clean(l.editor[i]));
   const candidate=[...new Map([...factual,...extra].map(t=>[t.toLocaleLowerCase(),t])).values()];
   const canonical=candidate.map(t=>t.toLocaleLowerCase()).sort().join(" ");
   if(sets[column].has(canonical))continue;
   sets[column].add(canonical);
   tags=candidate.sort((a,b)=>hash(h+":"+a)-hash(h+":"+b));break;
  }
  if(!tags)throw Error("Could not create a distinct factual hashtag combination; no sheet changes.");
  result.cells.push({column,value:tags.join(" ")});
 }
 output.push(result);
}
process.stdout.write(JSON.stringify({rows:output}));
