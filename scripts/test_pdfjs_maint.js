const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function run() {
  const data = new Uint8Array(require('fs').readFileSync('/Users/akshayryali/Downloads/LUBE OIL PUMP  SEIM-MANUAL (1).pdf'));
  const doc = await pdfjsLib.getDocument(data).promise;
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map(i => i.str).join(' ');
    if (text.toLowerCase().includes("maintenance")) {
      console.log(`PAGE ${i}:`);
      console.log(text.substring(0, 300) + "...");
    }
  }
}
run().catch(console.error);
