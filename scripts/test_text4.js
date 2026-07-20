const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function testAll() {
  const data2 = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  const doc2 = await pdfjsLib.getDocument({data: data2}).promise;
  const p10 = await doc2.getPage(10);
  const c10 = await p10.getTextContent();
  console.log(`\n--- Page 10 ---`);
  console.log(c10.items.map(item => item.str).join(" "));
}
testAll().catch(console.error);
