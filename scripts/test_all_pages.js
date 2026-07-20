const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function shouldProcessPageWithLLM(pageText) {
  if (!pageText) return false;
  const tocRegex = /(\.{5,}|\.\s\.\s\.\s\.\s\.)/g;
  const tocMatches = pageText.match(tocRegex);
  if (tocMatches && tocMatches.length > 4) {
    return false;
  }
  const text = pageText.toLowerCase();
  const keywords = ["replace", "lubricate", "grease", "inspect", "maintenance", "gearbox", "sump", "oil", "lubricant", "spare part", "part number", "part no", "drawing number", "drawing no", "model number", "model no", "qty", "illustrated parts list", "spare parts list", "bill of materials", "bom", "pos", "description"];
  return keywords.some(kw => text.includes(kw));
}

async function testAll() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  let passedPages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map(item => item.str).join(" ");
    if (shouldProcessPageWithLLM(text)) {
      passedPages.push(i);
    }
  }
  console.log("Pages that passed:", passedPages);
}
testAll().catch(console.error);
