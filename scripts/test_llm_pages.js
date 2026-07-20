const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function shouldProcessPageWithLLM(pageText) {
  if (!pageText) return false;
  const text = pageText.toLowerCase();
  const keywords = [
    "replace", "lubricate", "grease", "inspect", "check", "clean", "torque", "coaxiality", "tighten", "weld", "drain", "replenish", "maintenance", "interval",
    "bearing", "filter", "friction plate", "pad", "disc", "valve", "coupling", "seal", "clamp", "stopper", "gasket", "spring", "hose", "pipe", "pump", 
    "roller", "screw", "bolt", "nut", "pin", "wire", "rope", "plug", "motor", "gear", "reducer", "coupler", "fitting", "caliper", "drum", "shaft", 
    "gearbox", "sump", "oil", "grease", "lubricant", "spare part", "part number", "part no", "drawing number", "drawing no", "model number", "model no", "qty"
  ];
  return keywords.some(kw => text.includes(kw));
}

async function run() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/LUBE OIL PUMP  SEIM-MANUAL (1).pdf'));
  const doc = await pdfjsLib.getDocument(data).promise;
  const passedPages = [];
  for(let i=1; i<=doc.numPages; i++) {
     const page = await doc.getPage(i);
     const content = await page.getTextContent();
     const text = content.items.map(i => i.str).join(' ');
     if (shouldProcessPageWithLLM(text)) {
         passedPages.push(i);
     }
  }
  console.log("Pages passed:", passedPages);
}
run().catch(console.error);
