const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function extractText() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-1.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  let text = "";
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const strings = content.items.map(item => item.str);
    text += `\n--- Page ${i} ---\n` + strings.join(" ");
  }
  console.log(text);
}
extractText().catch(console.error);
