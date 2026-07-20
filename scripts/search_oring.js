const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function search() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map(item => item.str).join(" ").toLowerCase();
    if (text.includes("o-ring")) {
      console.log("Found O-Ring on page", i);
    }
    if (text.includes("pcb114c")) {
      console.log("Found PCB114C on page", i);
    }
  }
}
search().catch(console.error);
