const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function testAll() {
  const data2 = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  const doc2 = await pdfjsLib.getDocument({data: data2}).promise;
  for (let i = 7; i <= 9; i++) {
    const p2 = await doc2.getPage(i);
    const c2 = await p2.getTextContent();
    console.log(`\n--- Page ${i} ---`);
    console.log(c2.items.map(item => item.str).join(" ").substring(0, 500));
  }
}
testAll().catch(console.error);
