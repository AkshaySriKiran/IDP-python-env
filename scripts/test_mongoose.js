const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function shouldProcessPageWithLLM(pageText) {
  if (!pageText) return false;
  
  // Reject Table of Contents / Index pages to prevent LLM hallucination
  const tocRegex = /(\.{5,}|\.\s\.\s\.\s\.\s\.)/g;
  const tocMatches = pageText.match(tocRegex);
  if (tocMatches && tocMatches.length > 4) {
    return false;
  }

  const text = pageText.toLowerCase();
  const cleanText = text.replace(/\s+/g, ' ');
  
  const keywords = ["replace", "lubricate", "grease", "inspect", "maintenance", "gearbox", "sump", "oil", "lubricant", "spare part", "part number", "part no", "drawing number", "drawing no", "model number", "model no", "qty", "illustrated parts list", "spare parts list", "bill of materials", "bom", "pos", "description"];
  
  return keywords.some(kw => cleanText.includes(kw));
}

async function testAll() {
  const data1 = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-1.pdf'));
  const doc1 = await pdfjsLib.getDocument({data: data1}).promise;
  const p1 = await doc1.getPage(168);
  const c1 = await p1.getTextContent();
  const t1 = c1.items.map(item => item.str).join(" ");
  console.log("PDF1 Page 168 Gatekeeper:", shouldProcessPageWithLLM(t1));
  
  const data2 = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  const doc2 = await pdfjsLib.getDocument({data: data2}).promise;
  const p2 = await doc2.getPage(5);
  const c2 = await p2.getTextContent();
  const t2 = c2.items.map(item => item.str).join(" ");
  console.log("PDF2 Page 5 Gatekeeper:", shouldProcessPageWithLLM(t2));
}

testAll().catch(console.error);
