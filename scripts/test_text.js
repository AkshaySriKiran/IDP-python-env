const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function testAll() {
  const data2 = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  const doc2 = await pdfjsLib.getDocument({data: data2}).promise;
  const p2 = await doc2.getPage(5);
  const c2 = await p2.getTextContent();
  const t2 = c2.items.map(item => item.str).join(" ");
  console.log(t2);
}
testAll().catch(console.error);
