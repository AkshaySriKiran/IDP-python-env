const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function checkPage() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  const page = await doc.getPage(42);
  const textContent = await page.getTextContent();
  const pageText = textContent.items.map(item => item.str).join(" ");
  console.log(pageText);
}
checkPage().catch(console.error);
