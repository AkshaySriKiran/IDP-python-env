const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function isRecommendedSparePartsPage(pageText) {
  if (!pageText) return false;
  if (/\.{4,}/.test(pageText) || /\.\s*\.\s*\.\s*\.\s*\./.test(pageText) || pageText.toLowerCase().includes("table of contents")) {
    return false;
  }
  const text = pageText.toLowerCase();
  const cleanText = text.replace(/\s+/g, " ");
  return cleanText.includes("recommended (one year) spare parts") || 
         cleanText.includes("recommended spare parts") || 
         cleanText.includes("quick-wear parts") || 
         cleanText.includes("quick - wear parts") || 
         cleanText.includes("consumptive parts") || 
         cleanText.includes("quick-wear and consumptive") ||
         cleanText.includes("quick - wear and consumptive") ||
         cleanText.includes("bearings list of dw") ||
         (cleanText.includes("legend") && cleanText.includes("pos") && cleanText.includes("q.ty"));
}

async function check() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  const page = await doc.getPage(78);
  const textContent = await page.getTextContent();
  const pageText = textContent.items.map(item => item.str).join(" ");
  
  console.log("isRecommendedSparePartsPage for page 78 is:", isRecommendedSparePartsPage(pageText));
}

check().catch(console.error);
