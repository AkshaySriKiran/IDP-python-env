const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function run() {
  const data = new Uint8Array(require('fs').readFileSync('/Users/akshayryali/Downloads/LUBE OIL PUMP  SEIM-MANUAL (1).pdf'));
  const doc = await pdfjsLib.getDocument(data).promise;
  const page = await doc.getPage(33); // Try 33
  const content = await page.getTextContent();
  const text = content.items.map(i => i.str).join(' ');
  console.log("PAGE 33:");
  console.log(text);
}
run().catch(console.error);
