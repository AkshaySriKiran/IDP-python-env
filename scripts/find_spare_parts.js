const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function searchPdfs() {
  const dir = process.argv[2] || '/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/';
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.pdf'));
  
  for (const file of files) {
    const data = new Uint8Array(fs.readFileSync(dir + file));
    const doc = await pdfjsLib.getDocument({data}).promise;
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i);
      const content = await page.getTextContent();
      const text = content.items.map(item => item.str).join(" ").toLowerCase();
      if (text.includes("spare parts") || text.includes("parts list") || (text.includes("part no") && text.includes("qty"))) {
        console.log(`Found match in ${file} - Page ${i}`);
        console.log(text.substring(0, 300));
        console.log("------------------------");
      }
    }
  }
}
searchPdfs().catch(console.error);
